"""Provision project-owned demo data services from already imported Docker images.

Run as the authorized Linux host administrator. Never pulls images, prints secrets,
replaces foreign resources, or changes the upstream Astron/Dify deployments.
"""

import hashlib
import ipaddress
import json
import os
from contextlib import contextmanager
import fcntl
from pathlib import Path
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path('/opt/open-agent/control')
OWNER = 'open_agent.control.demo.v1'
LABEL = 'io.open-agent.owner'
NETWORK = 'open-agent-control-data'
MYSQL = 'm.daocloud.io/docker.io/library/mysql@sha256:a81d043930e51745522bd5c430961cf8286f0a23f0a9a2a6e0108f7ea71b0902'
SEAWEED = 'm.daocloud.io/docker.io/chrislusf/seaweedfs@sha256:57877e32bb90073b37dc109b438ba66e84fd5ed9ced5f5c04f56444ef243ce59'
BUCKET = 'open-agent-skills'
# Upstream V1.17/V1.18 explicitly qualify this schema; keep applied SQL immutable.
DATABASE = 'astron_console'


def command(*args, input=None, check=True, timeout=90):
    result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # Arguments, stdout and stderr can contain credentials. Keep diagnostics local.
        raise RuntimeError('Command failed: ' + args[0] + ' (exit ' + str(result.returncode) + ')')
    return result


def private_file(path, value, uid=0, gid=0, replace=False):
    """Create once; reruns must preserve credentials and reject unexpected changes."""
    if path.is_symlink():
        raise RuntimeError('Refusing a symlink at ' + str(path))
    content = value.encode()
    existing = path.read_bytes() if path.exists() else None
    if existing is not None and existing != content and not replace:
        raise RuntimeError('Existing configuration differs: ' + str(path))
    if existing != content:
        temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    os.chown(path, uid, gid)
    path.chmod(0o600)


@contextmanager
def installation_lock():
    descriptor = os.open('/opt/open-agent/.control-install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def data_dir(name, uid, gid):
    path = ROOT / name
    if path.is_symlink():
        raise RuntimeError('Refusing linked data directory')
    if not path.exists():
        path.mkdir(mode=0o700)
        os.chown(path, uid, gid)
    if path.stat().st_uid != uid or path.stat().st_gid != gid:
        raise RuntimeError('Unexpected data directory owner: ' + name)
    return path


def ensure_container(name, image, options, arguments, replace_owned=False):
    command('docker', 'image', 'inspect', image)
    specification_parts = [image, options, arguments]
    for index, option in enumerate(options):
        if option == '--env-file':
            specification_parts.append(hashlib.sha256(Path(options[index + 1]).read_bytes()).hexdigest())
    specification = hashlib.sha256(json.dumps(specification_parts).encode()).hexdigest()
    found = command('docker', 'container', 'inspect', name, check=False)
    exists = found.returncode == 0
    if exists:
        current = json.loads(found.stdout)[0]
        labels = current['Config'].get('Labels') or {}
        if labels.get(LABEL) != OWNER:
            raise RuntimeError('Container ownership/configuration mismatch: ' + name)
        if labels.get('io.open-agent.spec') != specification:
            if not replace_owned:
                raise RuntimeError('Container configuration changed: ' + name)
            command('docker', 'stop', '-t', '30', name)
            command('docker', 'rm', name)  # No volume/data removal.
            exists = False
        elif not current['State']['Running']:
            command('docker', 'start', name)
    if not exists:
        command('docker', 'run', '-d', '--name', name, '--label', LABEL + '=' + OWNER,
                '--label', 'io.open-agent.spec=' + specification, '--pull', 'never',
                '--restart', 'unless-stopped', '--network', NETWORK,
                '--log-driver', 'json-file', '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3',
                '--security-opt', 'no-new-privileges:true', '--cap-drop', 'ALL',
                '--pids-limit', '256', *options, image, *arguments)


def container_address(name):
    for _ in range(60):
        current = json.loads(command('docker', 'container', 'inspect', name).stdout)[0]
        if current['Config'].get('Labels', {}).get(LABEL) != OWNER:
            raise RuntimeError('Container ownership mismatch')
        address = current['NetworkSettings']['Networks'][NETWORK]['IPAddress']
        if address:
            if not ipaddress.ip_address(address).is_private:
                raise RuntimeError('Expected private data network address')
            return address
        time.sleep(1)
    raise RuntimeError('Container has not acquired a data network address')


def reconcile_firewalld():
    """Restore Docker's documented zone association for this owned bridge only."""
    if command('systemctl', 'is-active', '--quiet', 'firewalld', check=False).returncode:
        return
    state = json.loads(command('docker', 'network', 'inspect', NETWORK).stdout)[0]
    if state['Driver'] != 'bridge' or state.get('Labels', {}).get(LABEL) != OWNER:
        raise RuntimeError('Unexpected data network')
    bridge = state.get('Options', {}).get('com.docker.network.bridge.name') or 'br-' + state['Id'][:12]
    if not re.fullmatch(r'br-[0-9a-f]{12}', bridge):
        raise RuntimeError('Unexpected bridge interface')
    for permanent in (False, True):
        mode = ['--permanent'] if permanent else []
        zone = command('firewall-cmd', *mode, '--get-zone-of-interface=' + bridge, check=False).stdout.strip()
        if zone and zone not in ('no zone', 'docker'):
            raise RuntimeError('Bridge belongs to an unrelated firewall zone')
        if zone != 'docker':
            command('firewall-cmd', *mode, '--zone=docker', '--add-interface=' + bridge)


def initialize():
    if os.geteuid() != 0:
        raise RuntimeError('Requires authorized Linux host administrator')
    if any(path.is_symlink() for path in (ROOT, *ROOT.parents)):
        raise RuntimeError('Refusing linked installation path')
    marker = ROOT / '.owner'
    if ROOT.exists() and (not marker.is_file() or marker.read_text() != OWNER):
        raise RuntimeError('Directory is not owned by this installer')
    ROOT.mkdir(mode=0o700, exist_ok=True)
    ROOT.chmod(0o700)
    private_file(marker, OWNER)
    secret_path = ROOT / 'secrets.json'
    if secret_path.is_symlink():
        raise RuntimeError('Refusing linked credentials')
    if not secret_path.exists():
        values = {key: secrets.token_hex(32) for key in (
            'mysql_root', 'platform_db', 'identity_db', 's3_admin_secret', 's3_app_secret')}
        values.update(s3_admin_key='oa_admin_' + secrets.token_hex(12),
                      s3_app_key='oa_app_' + secrets.token_hex(12))
        private_file(secret_path, json.dumps(values, indent=2) + '\n')
    values = json.loads(secret_path.read_text())
    secret_path.chmod(0o600)
    network = command('docker', 'network', 'inspect', NETWORK, check=False)
    if network.returncode == 0:
        state = json.loads(network.stdout)[0]
        if state.get('Labels', {}).get(LABEL) != OWNER or not state['Internal'] or state['EnableIPv6']:
            raise RuntimeError('Network ownership/isolation mismatch')
    else:
        command('docker', 'network', 'create', '--internal', '--label', LABEL + '=' + OWNER, NETWORK)
    reconcile_firewalld()
    return values


def main():
    values = initialize()
    # These IDs are checked against the pinned official image before using host data.
    identity = command('docker', 'run', '--rm', '--network', 'none', '--pull', 'never',
                       '--entrypoint', 'id', MYSQL, 'mysql').stdout
    if 'uid=999(mysql)' not in identity or 'gid=999(mysql)' not in identity:
        raise RuntimeError('Unexpected MySQL image account')
    mysql_data = data_dir('mysql', 999, 999)
    private_file(ROOT / 'mysql-root', values['mysql_root'], 999, 999)
    # CREATE USER/GRANT statements contain only generated hex credentials.
    bootstrap = ''.join(
        f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{values[key]}';\n"
        f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{user}'@'%';\n"
        for database, user, key in [(DATABASE, 'open_agent', 'platform_db'),
                                    ('open_agent_identity', 'open_agent_identity', 'identity_db')])
    private_file(ROOT / 'mysql-init.sql', bootstrap, 999, 999, replace=True)
    ensure_container('open-agent-control-mysql', MYSQL, [
        '--memory', '640m', '--memory-swap', '640m', '--cpus', '1.25',
        '--user', '999:999', '--read-only', '--tmpfs', '/tmp:rw,nosuid,noexec,size=64m',
        '--tmpfs', '/var/run/mysqld:rw,nosuid,size=8m,uid=999,gid=999',
        '-v', f'{mysql_data}:/var/lib/mysql', '-v', f'{ROOT}/mysql-root:/run/secrets/mysql-root:ro',
        '-v', f'{ROOT}/mysql-init.sql:/docker-entrypoint-initdb.d/01-open-agent.sql:ro',
        '-e', 'MYSQL_ROOT_PASSWORD_FILE=/run/secrets/mysql-root',
    ], ['--innodb-buffer-pool-size=128M', '--max-connections=60', '--performance-schema=OFF',
        '--mysqlx=0', '--skip-name-resolve', '--log-error-verbosity=1'])
    for _ in range(90):
        ready = command('docker', 'exec', 'open-agent-control-mysql', 'sh', '-c',
                        'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -uroot -Nse "SELECT 1"', check=False)
        if ready.returncode == 0:
            break
        time.sleep(2)
    else:
        raise RuntimeError('MySQL did not become ready; inspect owned container logs')
    # Also applies CREATE IF NOT EXISTS/GRANT on an existing installation; never drops schemas.
    command('docker', 'exec', '-i', 'open-agent-control-mysql', 'sh', '-c',
            'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -uroot', input=bootstrap)
    print(json.dumps({'component': 'mysql', 'ready': True, 'public_port': False}), flush=True)

    storage_data = data_dir('seaweed', 65532, 65532)
    configuration = {'identities': [
        {'name': 'bootstrap-admin', 'credentials': [{'accessKey': values['s3_admin_key'],
          'secretKey': values['s3_admin_secret']}], 'actions': ['Admin', 'Read', 'Write', 'List']},
        {'name': 'platform-skills', 'credentials': [{'accessKey': values['s3_app_key'],
          'secretKey': values['s3_app_secret']}], 'actions': [f'{action}:{BUCKET}' for action in ('Read', 'Write', 'List')]},
    ]}
    private_file(ROOT / 's3.json', json.dumps(configuration, indent=2) + '\n', 65532, 65532)
    ensure_container('open-agent-control-s3', SEAWEED, [
        '--memory', '384m', '--memory-swap', '384m', '--cpus', '1',
        '--user', '65532:65532', '--read-only', '--tmpfs', '/tmp:rw,nosuid,noexec,size=32m',
        '--entrypoint', 'weed', '-e', 'GOMEMLIMIT=256MiB',
        '-v', f'{storage_data}:/data', '-v', f'{ROOT}/s3.json:/etc/seaweedfs/s3.json:ro',
    ], ['server', '-dir=/data', '-ip=127.0.0.1', '-ip.bind=127.0.0.1',
        '-s3', '-s3.ip.bind=0.0.0.0', '-s3.config=/etc/seaweedfs/s3.json',
        '-master.telemetry=false', '-master.volumeSizeLimitMB=64', '-volume.max=16',
        '-volume.minFreeSpace=10GiB', '-volume.fileSizeLimitMB=16', '-volume.index=leveldb',
        '-filer.exposeDirectoryData=false', '-filer.disableDirListing',
        '-s3.autoCreateBucket=false', '-s3.iam=false', '-s3.port.iceberg=0', '-s3.port.lance=0',
        '-s3.concurrentUploadLimitMB=16', '-s3.concurrentFileUploadLimit=4', '-s3.readerCacheSizeMB=16'])
    endpoint = 'http://' + container_address('open-agent-control-s3') + ':8333'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(60):
        try:
            opener.open(endpoint + '/', timeout=2).close()
            raise RuntimeError('S3 accepted an anonymous request')
        except urllib.error.HTTPError as error:
            if error.code == 403:
                break
            raise RuntimeError('Unexpected S3 response code: ' + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2)
    else:
        raise RuntimeError('S3 did not become ready')
    print(json.dumps({'component': 's3', 'anonymous_status': 403, 'public_port': False}), flush=True)
    deployment = {'owner': OWNER, 'network': NETWORK, 'mysql_image': MYSQL, 's3_image': SEAWEED,
                  's3_endpoint': 'http://open-agent-control-s3:8333', 's3_bucket': BUCKET,
                  'database': DATABASE, 'admission_enabled': False}
    private_file(ROOT / 'data-deployment.json', json.dumps(deployment, indent=2) + '\n', replace=True)


if __name__ == '__main__':
    with installation_lock():
        main()
