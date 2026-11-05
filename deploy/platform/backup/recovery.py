"""Owned demo cold backup. Uses Restic's encryption, locking and verification.

This is deliberately not a production HA/PITR implementation. No task is killed
to make a backup. Logs contain stable event codes, never subprocess diagnostics.
"""
import argparse
import bz2
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import time
import urllib.request

ROOT = Path('/opt/open-agent')
HOME = Path('/opt/open-agent-backup')
META = ROOT / 'recovery'
OWNER = 'open_agent.backup.demo.v1'
LABEL = 'io.open-agent.owner'
CONTROL_OWNER = 'open_agent.control.demo.v1'
BINARY = HOME / 'restic-0.19.1'
PASSWORD = HOME / 'password'
REPO = HOME / 'repository'
JOURNAL = HOME / 'maintenance.json'
ARCHIVE_SHA = 'f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c'
BINARY_SHA = '20d4142678d0d95ec11a4759def1b73fd9190abc9ca19e4b62d067c0b387e639'
LICENSE_SHA = '6f08a01a9fab5b24e139a09f15cc24a73087c7bc09e3bacf099fdf2d767bf897'
CONTAINERS = ['open-agent-control-api', 'open-agent-control-identity',
              'open-agent-control-s3', 'open-agent-control-mysql', 'open-agent-opensandbox']
STOP_UNITS = ['open-agent-supervisor.service', 'open-agent-trace-exporter.service',
              'open-agent-mlflow.service', 'open-agent-temporal.service',
              'open-agent-prometheus.service', 'open-agent-ingress.service']


def event(code, **values):
    print(json.dumps({'event': code, **values}), flush=True)


def utc():
    return datetime.now(timezone.utc).isoformat()


def run(*args, input=None, timeout=120, check=True, env=None):
    result = subprocess.run(args, input=input, capture_output=True, text=True,
                            timeout=timeout, env=env)
    if check and result.returncode:
        raise RuntimeError('command_failed:' + Path(args[0]).name)
    return result


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def private(path, value):
    if path.is_symlink():
        raise RuntimeError('linked_private_file')
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    path.chmod(0o600)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def save(path, value):
    private(path, json.dumps(value, indent=2) + '\n')


def owned_directory(path):
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
        raise RuntimeError('unsafe_owned_directory')


@contextmanager
def lock():
    owned_directory(ROOT)
    fd = os.open(ROOT / '.control-install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def installation():
    if os.geteuid() != 0:
        raise RuntimeError('root_required')
    owned_directory(HOME)
    if (HOME / '.owner').read_text().strip() != OWNER or digest(BINARY) != BINARY_SHA:
        raise RuntimeError('backup_installation_mismatch')
    if PASSWORD.is_symlink() or PASSWORD.stat().st_uid != 0 or PASSWORD.stat().st_mode & 0o077:
        raise RuntimeError('unsafe_backup_key')
    if REPO.is_symlink():
        raise RuntimeError('linked_backup_repository')


def restic(*args, timeout=1800):
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(HOME), 'GOMAXPROCS': '2',
           'RESTIC_REPOSITORY': str(REPO), 'RESTIC_PASSWORD_FILE': str(PASSWORD)}
    return run(str(BINARY), '--no-cache', *args, env=env, timeout=timeout).stdout


def install(companions):
    owned_directory(ROOT)
    if HOME.exists():
        owned_directory(HOME)
        if not (HOME / '.owner').exists() or (HOME / '.owner').read_text().strip() != OWNER:
            raise RuntimeError('unowned_backup_directory')
    else:
        HOME.mkdir(mode=0o700)
        private(HOME / '.owner', OWNER + '\n')
    archive = companions / 'restic_0.19.1_linux_amd64.bz2'
    notice = companions / 'restic-LICENSE'
    if digest(archive) != ARCHIVE_SHA or digest(notice) != LICENSE_SHA:
        raise RuntimeError('restic_artifact_checksum_mismatch')
    data = bz2.decompress(archive.read_bytes())
    if hashlib.sha256(data).hexdigest() != BINARY_SHA:
        raise RuntimeError('restic_binary_checksum_mismatch')
    if BINARY.is_symlink():
        raise RuntimeError('linked_backup_binary')
    BINARY.write_bytes(data)
    BINARY.chmod(0o700)
    private(HOME / 'LICENSE-restic', notice.read_text())
    if not PASSWORD.exists():
        if REPO.exists():
            raise RuntimeError('existing_repository_key_missing')
        private(PASSWORD, secrets.token_urlsafe(48) + '\n')
    installation()
    if not REPO.exists():
        restic('init')
    restic('snapshots', '--json')
    META.mkdir(mode=0o700, exist_ok=True)
    owned_directory(META)
    private(META / 'recovery.py', Path(__file__).read_text())
    private(META / 'LICENSE-restic', notice.read_text())
    event('backup_runtime_ready', version='0.19.1', binary_sha256=BINARY_SHA)


def inspect(name):
    item = json.loads(run('docker', 'inspect', name).stdout)[0]
    if name == 'open-agent-opensandbox':
        valid = item['Config'].get('Labels', {}).get('io.open-agent.component') == 'opensandbox-demo'
    else:
        valid = item['Config'].get('Labels', {}).get(LABEL) == CONTROL_OWNER
    if not valid or item['Name'] != '/' + name:
        raise RuntimeError('container_ownership_mismatch')
    return item


def sql(statement, container='open-agent-control-mysql'):
    return run('docker', 'exec', '-i', container, 'sh', '-c',
               'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -N -uroot astron_console',
               input=statement).stdout.strip()


def database_fingerprint(database, container='open-agent-control-mysql'):
    if database not in ('astron_console', 'open_agent_identity'):
        raise RuntimeError('database_not_owned')
    body = run('docker', 'exec', container, 'sh', '-c',
               'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysqldump -uroot '
               '--skip-comments --skip-dump-date --compact --no-create-info '
               '--skip-extended-insert --order-by-primary --hex-blob --set-gtid-purged=OFF ' + database,
               timeout=180).stdout
    return hashlib.sha256(body.encode()).hexdigest()


def assert_idle():
    counts = sql("SELECT (SELECT count(*) FROM oa_agent_run WHERE state IN "
                 "('queued','running','cancel_requested')), "
                 "(SELECT count(*) FROM oa_run_attempt WHERE released_at IS NULL);")
    if counts != '0\t0':
        raise RuntimeError('backup_requires_idle_tasks_and_released_attempts')


def unit_active(name):
    return run('systemctl', 'is-active', name, check=False).stdout.strip() == 'active'


def preflight():
    installation()
    if JOURNAL.exists() and json.loads(JOURNAL.read_text()).get('phase') != 'resumed':
        raise RuntimeError('resume_prior_maintenance_first')
    if shutil.disk_usage(HOME).free < 12 * 1024**3:
        raise RuntimeError('backup_insufficient_disk')
    containers = {name: inspect(name) for name in CONTAINERS}
    if any(not item['State']['Running'] for item in containers.values()):
        raise RuntimeError('backup_requires_healthy_demo')
    units = sorted(Path('/etc/systemd/system').glob('open-agent-*.service'))
    for path in units:
        if path.is_symlink() or 'open_agent' not in path.read_text().splitlines()[0]:
            raise RuntimeError('unit_ownership_mismatch')
    assert_idle()
    # An admitted sandbox must not survive a supposedly idle execution ledger.
    if run('docker', 'ps', '-q', '--filter', 'label=oa.platform=open-agent').stdout.strip():
        raise RuntimeError('active_sandbox_prevents_backup')
    return containers, units


def wait_http(endpoint, seconds=180):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            with opener.open(endpoint, timeout=3) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError('recovery_readiness_timeout')


def resume():
    installation()
    if not JOURNAL.exists():
        raise RuntimeError('maintenance_journal_missing')
    state = json.loads(JOURNAL.read_text())
    if state.get('owner') != OWNER:
        raise RuntimeError('maintenance_owner_mismatch')
    if set(state['containers']) - set(CONTAINERS) or set(state['units']) - set(STOP_UNITS):
        raise RuntimeError('maintenance_scope_mismatch')
    errors = []
    for name in ['open-agent-control-mysql', 'open-agent-control-s3', 'open-agent-control-identity',
                 'open-agent-opensandbox', 'open-agent-control-api']:
        if name in state['containers']:
            try:
                inspect(name)
                run('docker', 'start', name)
            except Exception:
                errors.append('container_resume')
    for name in ['open-agent-temporal.service', 'open-agent-mlflow.service',
                 'open-agent-trace-exporter.service', 'open-agent-supervisor.service',
                 'open-agent-prometheus.service', 'open-agent-ingress.service']:
        if name in state['units']:
            try:
                run('systemctl', 'start', name)
            except Exception:
                errors.append('unit_resume')
    if 'open-agent-control-api' in state['containers']:
        try:
            wait_http('http://127.0.0.1:18082/health/ready')
        except Exception:
            errors.append('api_readiness')
    if 'open-agent-supervisor.service' in state['units']:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            ready = ROOT / 'supervisor/ready.json'
            pid = run('systemctl', 'show', 'open-agent-supervisor.service', '-p', 'MainPID', '--value').stdout.strip()
            if ready.exists() and str(json.loads(ready.read_text())['pid']) == pid:
                break
            time.sleep(2)
        else:
            errors.append('supervisor_readiness')
    if errors:
        state.update(phase='resume_failed', failures=sorted(set(errors)))
        save(JOURNAL, state)
        raise RuntimeError('original_service_resume_incomplete')
    state.update(phase='resumed', resumed_at=utc())
    save(JOURNAL, state)
    event('original_services_resumed')


def backup():
    containers, units = preflight()
    state = {'owner': OWNER, 'phase': 'stopping', 'started_at': utc(),
             'containers': [n for n, c in containers.items() if c['State']['Running']],
             'units': [n for n in STOP_UNITS if unit_active(n)]}
    save(JOURNAL, state)  # Persist original state before the first stop.
    started = time.monotonic()
    summary = None
    try:
        run('docker', 'stop', '-t', '120', 'open-agent-control-api', timeout=150)
        assert_idle()  # Closes the admission race between preflight and API shutdown.
        for unit in state['units']:
            run('systemctl', 'stop', unit, timeout=180)
        run('docker', 'stop', '-t', '60', 'open-agent-control-identity', 'open-agent-opensandbox', timeout=150)
        manifest = {'schema': 1, 'data_cutoff': utc(), 'containers': containers,
                    'database_fingerprints': {d: database_fingerprint(d) for d in
                                              ('astron_console', 'open_agent_identity')},
                    'units': {str(p): digest(p) for p in units}}
        META.mkdir(mode=0o700, exist_ok=True)
        owned_directory(META)
        save(META / 'manifest.json', manifest)
        run('docker', 'stop', '-t', '120', 'open-agent-control-s3', 'open-agent-control-mysql', timeout=270)
        for name in ['open-agent-control-mysql', 'open-agent-control-s3']:
            if inspect(name)['State']['ExitCode'] != 0:
                raise RuntimeError('data_service_unclean_shutdown')
        state['phase'] = 'cold'
        save(JOURNAL, state)
        excludes = [ROOT / p for p in ('verification', 'cache', 'observability/cache',
                    'observability/bundles', 'sandbox/verification-venv', 'agent-worker/process-verification')]
        current_jar = next(m['Source'] for m in containers['open-agent-control-api']['Mounts']
                           if m['Destination'] == '/app/platform.jar')
        excludes += [p for p in (ROOT / 'control/releases').iterdir() if str(p / 'platform.jar') != current_jar]
        args = ['backup', '--json', '--host', 'open-agent-demo', '--tag', 'consistent-cold-v1']
        for path in excludes:
            args += ['--exclude', str(path)]
        args += [str(ROOT), '/var/lib/open-agent-mlflow', *map(str, units)]
        output = restic(*args)
        summary = next(json.loads(line) for line in output.splitlines()
                       if json.loads(line).get('message_type') == 'summary')
        if not summary.get('snapshot_id'):
            raise RuntimeError('backup_snapshot_missing')
        save(HOME / 'last-backup.json', {'snapshot_id': summary['snapshot_id'],
             'data_cutoff': manifest['data_cutoff'], 'started_at': state['started_at']})
        event('encrypted_snapshot_created', snapshot_id=summary['snapshot_id'])
    finally:
        resume()
    elapsed = round(time.monotonic() - started, 3)
    # Read every encrypted pack, not just repository metadata. Services are online.
    restic('check', '--read-data')
    report = json.loads((HOME / 'last-backup.json').read_text())
    report.update(verified_at=utc(), maintenance_seconds=elapsed,
                  files=summary['total_files_processed'], source_bytes=summary['total_bytes_processed'],
                  all_pack_data_verified=True, repository_encrypted=True)
    save(HOME / 'backup-report.json', report)
    event('backup_verified', **report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'backup', 'resume', 'check'])
    parser.add_argument('--companions', type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    os.umask(0o077)
    # Normal cancellation still runs the resume finally block; SIGKILL/reboot
    # requires the durable `resume` command before another backup is allowed.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError('maintenance_interrupted')))
    with lock():
        if args.action == 'install':
            install(args.companions)
        elif args.action == 'backup':
            backup()
        elif args.action == 'resume':
            resume()
        else:
            installation()
            restic('check', '--read-data')
            event('repository_verified')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        event('backup_failed', error_type=type(error).__name__)
        raise SystemExit(1) from None
