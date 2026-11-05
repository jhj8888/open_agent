"""Start the verified standalone control plane with real private dependencies.

Admission is enabled only by an explicit owned installation record and a ready
execution supervisor. No management or execution APIs are exposed publicly.
"""

import base64
import hashlib
import json
from pathlib import Path
import secrets
import socket
import time
import urllib.error
import urllib.request

from provision_data import ROOT, LABEL, OWNER, command, container_address, ensure_container, initialize, private_file, installation_lock, reconcile_firewalld

JRE = 'm.daocloud.io/docker.io/library/eclipse-temurin@sha256:1eac8b8fee11ca7d6725a48bc5d41992ed1561504ebae25490d2d062847e8269'
ARTIFACT_SHA256 = 'a16d4ad586a2a5369ef0d28f89d298523b60fba43986260a1b8d746c49fadb7d'
API_NETWORK = 'open-agent-control-api'


def prepare_api_network():
    # The data network stays internal. Only the API joins this additional network
    # for the loopback worker endpoint and administrator-approved demo model probes.
    found = command('docker', 'network', 'inspect', API_NETWORK, check=False)
    if found.returncode:
        command('docker', 'network', 'create', '--driver', 'bridge',
                '--opt', 'com.docker.network.bridge.enable_icc=false', '--label', LABEL + '=' + OWNER, API_NETWORK)
    network = json.loads(command('docker', 'network', 'inspect', API_NETWORK).stdout)[0]
    if (network.get('Labels', {}).get(LABEL) != OWNER or network['Driver'] != 'bridge'
            or network['Internal'] or network['EnableIPv6']):
        raise RuntimeError('API network ownership/configuration mismatch')
    reconcile_firewalld(API_NETWORK)
    previous = command('docker', 'container', 'inspect', 'open-agent-control-api', check=False)
    owned_binding = False
    if previous.returncode == 0:
        current = json.loads(previous.stdout)[0]
        if current['Config'].get('Labels', {}).get(LABEL) != OWNER:
            raise RuntimeError('API container ownership mismatch')
        owned_binding = {'HostIp': '127.0.0.1', 'HostPort': '18082'} in ((current['HostConfig'].get('PortBindings') or {}).get('18082/tcp') or [])
    if not owned_binding:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 18082))


def main():
    values = initialize()
    jar = ROOT / 'releases' / ARTIFACT_SHA256 / 'platform.jar'
    if jar.is_symlink() or hashlib.sha256(jar.read_bytes()).hexdigest() != ARTIFACT_SHA256:
        raise RuntimeError('Control-plane artifact checksum mismatch')
    prepare_api_network()
    identity = json.loads((ROOT / 'identity-deployment.json').read_text())
    storage = json.loads((ROOT / 'data-deployment.json').read_text())
    key_path = ROOT / 'api-secrets.json'
    if key_path.is_symlink():
        raise RuntimeError('Linked API secrets are not allowed')
    if not key_path.exists():
        private_file(key_path, json.dumps({'model_encryption_key': base64.b64encode(secrets.token_bytes(32)).decode(),
                                         'worker_key': secrets.token_urlsafe(40),
                                         'dispatcher_key': secrets.token_urlsafe(40)}, indent=2) + '\n')
    api_secrets = json.loads(key_path.read_text())
    if 'reconciler_key' not in api_secrets:
        api_secrets['reconciler_key'] = secrets.token_urlsafe(40)
        private_file(key_path, json.dumps(api_secrets, indent=2) + '\n', replace=True)
    if 'observer_key' not in api_secrets:
        api_secrets['observer_key'] = secrets.token_urlsafe(40)
        private_file(key_path, json.dumps(api_secrets, indent=2) + '\n', replace=True)
    if 'trace_exporter_key' not in api_secrets:
        api_secrets['trace_exporter_key'] = secrets.token_urlsafe(40)
        private_file(key_path, json.dumps(api_secrets, indent=2) + '\n', replace=True)
    admission = False
    admission_record = ROOT / 'execution-admission.json'
    if admission_record.exists():
        if admission_record.is_symlink() or admission_record.stat().st_uid != 0 or admission_record.stat().st_mode & 0o077:
            raise RuntimeError('Unsafe execution admission record')
        enabled = json.loads(admission_record.read_text())
        if enabled != {'enabled': True, 'supervisor': 'demo-supervisor'}:
            raise RuntimeError('Unexpected execution admission record')
        ready = json.loads(Path('/opt/open-agent/supervisor/ready.json').read_text())
        pid = command('systemctl', 'show', 'open-agent-supervisor.service', '-p', 'MainPID', '--value').stdout.strip()
        if str(ready['pid']) != pid or pid == '0':
            raise RuntimeError('Execution supervisor is not ready')
        admission = True
    environment = {
        'PLATFORM_BIND_ADDRESS': '0.0.0.0', 'PLATFORM_PORT': '18082',
        'PLATFORM_JDBC_URL': 'jdbc:mysql://open-agent-control-mysql:3306/' + storage['database'] + '?useUnicode=true&characterEncoding=utf8&serverTimezone=UTC&forceConnectionTimeZoneToSession=true&allowPublicKeyRetrieval=true&useSSL=false',
        'PLATFORM_DB_USER': 'open_agent', 'PLATFORM_DB_PASSWORD': values['platform_db'],
        'PLATFORM_OIDC_ISSUER': identity['issuer'], 'PLATFORM_OIDC_JWKS': identity.get('internal_jwks', identity['issuer'] + '/.well-known/jwks'),
        'PLATFORM_OIDC_AUDIENCE': identity['client_id'],
        'PLATFORM_S3_ENDPOINT': storage['s3_endpoint'], 'PLATFORM_S3_BUCKET': storage['s3_bucket'],
        'PLATFORM_S3_ACCESS_KEY': values['s3_app_key'], 'PLATFORM_S3_SECRET_KEY': values['s3_app_secret'],
        'MODEL_CREDENTIAL_ACTIVE_KEY': 'v1', 'MODEL_CREDENTIAL_KEYS': json.dumps({'v1': api_secrets['model_encryption_key']}),
        'PLATFORM_RUN_ADMISSION_ENABLED': str(admission).lower(), 'LANGSMITH_TRACING': 'false',
        'MODEL_ALLOWED_ORIGINS': json.dumps(['https://api.deepseek.com']),
        'MODEL_PUBLIC_DEMO_ORIGINS': json.dumps(['https://api.deepseek.com']),
        'PLATFORM_PROMETHEUS_URL': 'http://' + json.loads(command('docker','network','inspect',API_NETWORK).stdout)[0]['IPAM']['Config'][0]['Gateway'] + ':19090',
        'PLATFORM_SERVICE_KEYS': json.dumps({
            'demo-worker': {'role': 'worker', 'secret': api_secrets['worker_key']},
            'demo-dispatcher': {'role': 'dispatcher', 'secret': api_secrets['dispatcher_key']},
            'demo-supervisor': {'role': 'reconciler', 'secret': api_secrets['reconciler_key']},
            'demo-observer': {'role': 'observer', 'secret': api_secrets['observer_key']},
            'demo-trace-exporter': {'role': 'trace_exporter', 'secret': api_secrets['trace_exporter_key']},
        }),
        'PLATFORM_RECONCILER_WORKERS': json.dumps({'demo-supervisor': 'demo-worker'}),
    }
    sandbox_record = Path('/opt/open-agent/supervisor/sandbox-enabled.json')
    if sandbox_record.exists():
        sandbox = json.loads(Path('/opt/open-agent/sandbox/deployment.json').read_text())
        if sandbox['profile'] != 'gvisor-demo' or sandbox['opensandbox'] != '1.1.0':
            raise RuntimeError('Unexpected Skill sandbox installation')
        environment['SKILL_RUNTIME_IMAGES'] = json.dumps([sandbox['runtime_image']])
    private_file(ROOT / 'api.env', ''.join(key + '=' + value + '\n' for key, value in environment.items()), replace=True)
    ensure_container('open-agent-control-api', JRE, [
        '--publish', '127.0.0.1:18082:18082/tcp',
        '--memory', '896m', '--memory-swap', '896m', '--cpus', '1.5', '--user', '65531:65531',
        '--read-only', '--tmpfs', '/tmp:rw,nosuid,noexec,size=64m,uid=65531,gid=65531',
        '--env-file', str(ROOT / 'api.env'), '--entrypoint', 'java',
        '-v', f'{jar}:/app/platform.jar:ro',
    ], ['-Xms64m', '-Xmx448m', '-XX:MaxMetaspaceSize=192m', '-XX:MaxDirectMemorySize=96m',
        '-XX:+ExitOnOutOfMemoryError', '-jar', '/app/platform.jar'], replace_owned=True, extra_networks=(API_NETWORK,))
    endpoint = 'http://' + container_address('open-agent-control-api') + ':18082'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(120):
        try:
            with opener.open(endpoint + '/health/ready', timeout=3) as response:
                status = json.load(response)
            if status.get('status') != 'UP':
                raise RuntimeError('Unexpected control-plane readiness response')
            break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2)
    else:
        raise RuntimeError('API did not become ready; inspect owned logs')
    with opener.open('http://127.0.0.1:18082/health/ready', timeout=5) as response:
        if json.load(response).get('status') != 'UP':
            raise RuntimeError('Loopback worker endpoint is unavailable')
    for route in ['/platform/tenants', '/internal/platform/dispatch/pending']:
        request = urllib.request.Request(endpoint + route, headers={'X-User-Id': 'admin', 'Authorization': 'Bearer forged'})
        try:
            opener.open(request, timeout=5).close()
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise RuntimeError('Unexpected unauthorized API response') from None
        else:
            raise RuntimeError('API accepted an unauthorized request')
    result = {'artifact_sha256': ARTIFACT_SHA256, 'jre_image': JRE, 'database_ready': True,
              'forged_identity_rejected': True, 'admission_enabled': admission, 'public_port': False,
              'loopback_endpoint': 'http://127.0.0.1:18082'}
    private_file(ROOT / 'api-deployment.json', json.dumps(result, indent=2) + '\n', replace=True)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    with installation_lock():
        main()
