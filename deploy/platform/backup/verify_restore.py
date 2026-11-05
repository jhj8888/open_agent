"""Restore a verified cold snapshot into an isolated, execution-disabled copy.

No live volume is mounted into recovery containers. A failed drill keeps its
private files for diagnosis; owned containers/network are always removed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import time
import urllib.parse
from uuid import uuid4

import recovery as r

sys.path.append(str(Path(__file__).resolve().parents[1] / 'control'))
sys.path.append('/opt/open-agent/verification/control-package')
from demo_session import DemoSession
from verify_task_outputs import raw

DRILL_OWNER = 'open_agent.backup.restore.v1'
DRILL_JOURNAL = r.HOME / 'restore-maintenance.json'


def network_guard(state, install):
    bridge = state.get('bridge')
    if not bridge:
        return
    if not re.fullmatch(r'br-[0-9a-f]{12}', bridge):
        raise RuntimeError('restore_bridge_invalid')
    comment = DRILL_OWNER + ':' + state['id']
    rules = [('INPUT', ['-i', bridge, '-m', 'conntrack', '--ctstate', 'NEW',
                       '-m', 'comment', '--comment', comment, '-j', 'REJECT']),
             ('DOCKER-USER', ['-i', bridge, '!', '-o', bridge,
                              '-m', 'comment', '--comment', comment, '-j', 'DROP'])]
    for chain, rule in rules:
        present = r.run('iptables', '-w', '10', '-C', chain, *rule, check=False).returncode == 0
        if install and not present:
            r.run('iptables', '-w', '10', '-I', chain, '1', *rule)
        elif not install and present:
            r.run('iptables', '-w', '10', '-D', chain, *rule)
    if r.unit_active('firewalld'):
        zone = r.run('firewall-cmd', '--get-zone-of-interface=' + bridge, check=False).stdout.strip()
        if install:
            if zone not in ('', 'no zone', 'docker'):
                raise RuntimeError('restore_bridge_in_foreign_zone')
            state['zone_added'] = zone != 'docker'
            r.save(DRILL_JOURNAL, state)
            if state['zone_added']:
                # Docker's documented zone association; runtime only for this
                # temporary bridge. Host/other-network access remains denied.
                r.run('firewall-cmd', '--zone=docker', '--add-interface=' + bridge)
        elif state.get('zone_added') and zone == 'docker':
            r.run('firewall-cmd', '--zone=docker', '--remove-interface=' + bridge)


def cleanup():
    state = json.loads(DRILL_JOURNAL.read_text())
    if state.get('owner') != DRILL_OWNER or not re.fullmatch(r'[0-9a-f]{12}', state['id']):
        raise RuntimeError('restore_journal_owner_mismatch')
    expected = ['oa-restore-' + state['id'] + '-' + c for c in ('mysql', 's3', 'identity', 'api')]
    if state['containers'] != expected or state['network'] != 'oa-restore-' + state['id']:
        raise RuntimeError('restore_journal_scope_mismatch')
    if set(state['paused']) - {'open-agent-trace-exporter.service', 'open-agent-mlflow.service'}:
        raise RuntimeError('restore_journal_unit_mismatch')
    errors = []
    try:
        for name in reversed(expected):
            result = r.run('docker', 'inspect', name, check=False)
            if result.returncode:
                continue
            item = json.loads(result.stdout)[0]
            if item['Config'].get('Labels', {}).get(r.LABEL) != DRILL_OWNER:
                raise RuntimeError('restore_cleanup_owner_mismatch')
            r.run('docker', 'rm', '-f', name)
        network_guard(state, False)
        found = r.run('docker', 'network', 'inspect', state['network'], check=False)
        if not found.returncode:
            if json.loads(found.stdout)[0].get('Labels', {}).get(r.LABEL) != DRILL_OWNER:
                raise RuntimeError('restore_cleanup_network_owner_mismatch')
            r.run('docker', 'network', 'rm', state['network'])
    except Exception:
        errors.append('clone_cleanup')
    # Attempt every original service even if a previous cleanup/start failed.
    for unit in reversed(state['paused']):
        try:
            r.run('systemctl', 'start', unit)
        except Exception:
            errors.append('service_resume')
    if errors:
        raise RuntimeError('restore_cleanup_incomplete')
    state.update(phase='cleaned', cleaned_at=r.utc())
    r.save(DRILL_JOURNAL, state)
    r.event('restore_services_resumed_and_clones_removed')


def address(name):
    item = json.loads(r.run('docker', 'inspect', name).stdout)[0]
    networks = item['NetworkSettings']['Networks']
    if len(networks) != 1 or item['HostConfig'].get('PortBindings'):
        raise RuntimeError('restore_network_or_port_mismatch')
    return next(iter(networks.values()))['IPAddress']


def clone(original, target, network, identifier):
    source_name = original['Name'].removeprefix('/')
    if source_name not in r.CONTAINERS[:4]:
        raise RuntimeError('restore_container_out_of_scope')
    name = 'oa-restore-' + identifier + '-' + source_name.removeprefix('open-agent-control-')
    image = original['Config']['Image']
    if '@sha256:' not in image:
        raise RuntimeError('restore_image_not_pinned')
    r.run('docker', 'image', 'inspect', image)  # Never pull during recovery.
    environment = dict(entry.split('=', 1) for entry in original['Config']['Env'])
    if source_name.endswith('-api'):
        jwks = urllib.parse.urlsplit(environment['PLATFORM_OIDC_JWKS'])
        environment.update(PLATFORM_RUN_ADMISSION_ENABLED='false', PLATFORM_SERVICE_KEYS='{}',
                           PLATFORM_RECONCILER_WORKERS='{}', MODEL_ALLOWED_ORIGINS='[]',
                           MODEL_PUBLIC_DEMO_ORIGINS='[]', PLATFORM_PROMETHEUS_URL='http://127.0.0.1:9',
                           PLATFORM_OIDC_JWKS='http://open-agent-control-identity:8000' + jwks.path)
    envfile = target / (name + '.env')
    r.private(envfile, ''.join(k + '=' + v + '\n' for k, v in environment.items()))
    host = original['HostConfig']
    args = ['docker', 'create', '--name', name, '--network', network,
            '--network-alias', source_name, '--label', r.LABEL + '=' + DRILL_OWNER,
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--pids-limit', '256', '--memory', str(host['Memory']), '--memory-swap', str(host['Memory']),
            '--cpus', '1', '--log-driver', 'json-file', '--log-opt', 'max-size=2m', '--log-opt', 'max-file=1',
            '--env-file', str(envfile)]
    if original['Config'].get('User'):
        args += ['--user', original['Config']['User']]
    for dest, options in (host.get('Tmpfs') or {}).items():
        args += ['--tmpfs', dest + ':' + options]
    for mount in original['Mounts']:
        source = Path(mount['Source'])
        if mount['Type'] != 'bind' or not source.is_relative_to(r.ROOT):
            raise RuntimeError('restore_mount_out_of_scope')
        restored = target / source.relative_to('/')
        if not restored.exists() or not restored.resolve().is_relative_to(target.resolve()):
            raise RuntimeError('restore_mount_missing_or_escaped')
        args += ['--mount', 'type=bind,src=' + str(restored) + ',dst=' + mount['Destination']
                 + (',readonly' if not mount['RW'] else '')]
    entrypoint = original['Config']['Entrypoint'] or []
    if entrypoint:
        args += ['--entrypoint', entrypoint[0]]
    args += [image, *entrypoint[1:], *(original['Config']['Cmd'] or [])]
    r.run(*args)
    return name


def sqlite_checks(target):
    files = sorted((target / 'var/lib/open-agent-mlflow').glob('*.db'))
    files += sorted((target / 'opt/open-agent/temporal/state').glob('*.db'))
    files += sorted((target / 'opt/open-agent/sandbox/state').glob('*.db'))
    if len(files) < 3:
        raise RuntimeError('restored_sqlite_state_missing')
    results = []
    for path in files:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise RuntimeError('restored_sqlite_integrity_failed')
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            counts = {table: db.execute('SELECT count(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]
                      for table in tables}
        results.append({'path': str(path.relative_to(target)), 'integrity': 'ok', 'row_counts': counts})
    return results


def verify():
    r.installation()
    r.assert_idle()
    if DRILL_JOURNAL.exists() and json.loads(DRILL_JOURNAL.read_text()).get('phase') != 'cleaned':
        raise RuntimeError('cleanup_previous_restore_first')
    if r.JOURNAL.exists() and json.loads(r.JOURNAL.read_text())['phase'] != 'resumed':
        raise RuntimeError('live_maintenance_incomplete')
    report = json.loads((r.HOME / 'backup-report.json').read_text())
    snapshot = report['snapshot_id']
    if len(snapshot) != 8 and len(snapshot) != 64 or any(c not in '0123456789abcdef' for c in snapshot):
        raise RuntimeError('invalid_snapshot_id')
    identifier = uuid4().hex[:12]
    parent = r.HOME / 'drills'
    parent.mkdir(mode=0o700, exist_ok=True)
    r.owned_directory(parent)
    target = parent / identifier
    target.mkdir(mode=0o700)
    network = 'oa-restore-' + identifier
    containers = []
    started = time.monotonic()
    paused = [u for u in ['open-agent-trace-exporter.service', 'open-agent-mlflow.service'] if r.unit_active(u)]
    state = {'owner': DRILL_OWNER, 'id': identifier, 'phase': 'restoring', 'paused': paused,
             'containers': ['oa-restore-' + identifier + '-' + c for c in ('mysql', 's3', 'identity', 'api')],
             'network': network, 'started_at': r.utc()}
    r.save(DRILL_JOURNAL, state)
    try:
        r.restic('check', '--read-data')
        r.restic('restore', snapshot, '--target', str(target), '--verify')
        r.event('snapshot_files_restored_and_verified')
        restore_seconds = round(time.monotonic() - started, 3)
        manifest = json.loads((target / 'opt/open-agent/recovery/manifest.json').read_text())
        database_checks = sqlite_checks(target)
        for unit, expected in manifest['units'].items():
            if r.digest(target / Path(unit).relative_to('/')) != expected:
                raise RuntimeError('restored_unit_checksum_mismatch')
        # Temporarily free the demo observation backend's ~500 MiB. The durable
        # outbox queues observations; execution and control APIs remain online.
        for unit in paused:
            r.run('systemctl', 'stop', unit)
        available = next(int(l.split()[1]) * 1024 for l in Path('/proc/meminfo').read_text().splitlines()
                         if l.startswith('MemAvailable:'))
        if available < 1800 * 1024**2:
            raise RuntimeError('restore_insufficient_memory')
        r.run('docker', 'network', 'create', '--internal', '--label', r.LABEL + '=' + DRILL_OWNER, network)
        network_state = json.loads(r.run('docker', 'network', 'inspect', network).stdout)[0]
        if not network_state['Internal'] or network_state['EnableIPv6']:
            raise RuntimeError('restore_network_has_external_route')
        state['bridge'] = 'br-' + network_state['Id'][:12]
        r.save(DRILL_JOURNAL, state)
        network_guard(state, True)
        for component in ['mysql', 's3', 'identity', 'api']:
            original = manifest['containers']['open-agent-control-' + component]
            name = clone(original, target, network, identifier)
            containers.append(name)
            r.run('docker', 'start', name)
            if component == 'mysql':
                for _ in range(60):
                    try:
                        if r.sql('SELECT 1;', name) == '1':
                            break
                    except RuntimeError:
                        pass
                    time.sleep(2)
                else:
                    raise RuntimeError('restored_mysql_not_ready')
                for database, expected in manifest['database_fingerprints'].items():
                    if r.database_fingerprint(database, name) != expected:
                        raise RuntimeError('restored_database_content_mismatch')
                r.event('restored_databases_match_snapshot')
            elif component == 'identity':
                r.wait_http('http://' + address(name) + ':8000/.well-known/jwks')
            elif component == 'api':
                r.wait_http('http://' + address(name) + ':18082/health/ready')
        state_root = target / 'opt/open-agent/control'
        arguments = dict(api='http://' + address(containers[-1]) + ':18082',
                         identity_endpoint='http://' + address(containers[-2]) + ':8000', state_root=state_root)
        session = DemoSession(**arguments)
        other = DemoSession('member-demo', json.loads((state_root / 'member-secrets.json').read_text())['password'],
                            require_tenant=False, **arguments)
        # Every available output owned by the authenticated demonstration tenant
        # is fetched through the actual authorized Java -> S3 download path.
        rows = r.sql("SELECT run_id,output_id,sha256,size_bytes FROM oa_run_output "
                     "WHERE state='available' AND tenant_id='space-1' ORDER BY output_id;", containers[0]).splitlines()
        files = []
        for line in rows:
            run_id, output_id, sha, size = line.split('\t')
            route = f'/api/v1/runs/{run_id}/outputs/{output_id}/download'
            body, _ = raw(session, route)
            if hashlib.sha256(body).hexdigest() != sha or len(body) != int(size):
                raise RuntimeError('restored_output_checksum_mismatch')
            raw(other, route, 404)
            files.append({'run_id': run_id, 'output_id': output_id, 'bytes': int(size), 'sha256': sha})
        if not files:
            raise RuntimeError('restore_no_outputs_verified')
        # Actual task-step payloads, including inputs/thinking/tools, survive the
        # database fingerprint check; additionally exercise authorized paging.
        run_ids = sorted({item['run_id'] for item in files})
        steps = 0
        for run_id in run_ids:
            after = 0
            while True:
                page = session.call(f'/api/v1/runs/{run_id}/observations?after={after}&limit=200')
                steps += len(page['items'])
                if not page['hasMore']:
                    break
                after = page['next']
            raw(other, f'/api/v1/runs/{run_id}/observations', 404)
        if not steps:
            raise RuntimeError('restore_no_observations_verified')
        result = {'verified_at': r.utc(), 'snapshot_id': snapshot, 'data_cutoff': manifest['data_cutoff'],
                  'restore_and_integrity_seconds': restore_seconds,
                  'restore_to_verified_api_seconds': round(time.monotonic() - started, 3),
                  'database_fingerprints_equal': True, 'real_oidc_login': True,
                  'all_restored_files_verified_by_restic': True, 'cross_tenant_download_and_steps_denied': True,
                  'execution_admission': False, 'worker_started': False, 'internal_network_only': True,
                  'host_ports_published': False, 'files': files, 'observed_steps': steps,
                  'sqlite_checks': database_checks, 'restored_directory': str(target),
                  'completed_tasks_lost_at_snapshot': 0}
        r.save(r.HOME / 'restore-report.json', result)
        r.event('isolated_restore_verified', files=len(files), observed_steps=steps,
                restore_to_verified_api_seconds=result['restore_to_verified_api_seconds'])
        return result
    finally:
        cleanup()


if __name__ == '__main__':
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError('restore_interrupted')))
    try:
        with r.lock():
            if sys.argv[1:] == ['--cleanup']:
                cleanup()
            elif sys.argv[1:]:
                raise RuntimeError('unexpected_restore_arguments')
            else:
                verify()
    except Exception as error:
        r.event('restore_failed', error_type=type(error).__name__,
                error_code=str(error) if isinstance(error, RuntimeError) else 'unexpected_error')
        raise SystemExit(1) from None
