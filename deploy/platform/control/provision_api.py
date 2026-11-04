"""Start the verified standalone control plane with real private dependencies.

Admission remains disabled until durable execution and physical resource cleanup
are installed. This script does not expose management or execution APIs publicly.
"""

import base64
import hashlib
import json
from pathlib import Path
import secrets
import time
import urllib.error
import urllib.request

from provision_data import ROOT, container_address, ensure_container, initialize, private_file, installation_lock

JRE = 'm.daocloud.io/docker.io/library/eclipse-temurin@sha256:1eac8b8fee11ca7d6725a48bc5d41992ed1561504ebae25490d2d062847e8269'
ARTIFACT_SHA256 = '05dda8bf769f25a02bd5edada88a7ead3ea79fde4338f3d1ef765dbd37d3d9fa'


def main():
    values = initialize()
    jar = ROOT / 'releases' / ARTIFACT_SHA256 / 'platform.jar'
    if jar.is_symlink() or hashlib.sha256(jar.read_bytes()).hexdigest() != ARTIFACT_SHA256:
        raise RuntimeError('Control-plane artifact checksum mismatch')
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
    environment = {
        'PLATFORM_BIND_ADDRESS': '0.0.0.0', 'PLATFORM_PORT': '18082',
        'PLATFORM_JDBC_URL': 'jdbc:mysql://open-agent-control-mysql:3306/' + storage['database'] + '?useUnicode=true&characterEncoding=utf8&serverTimezone=UTC&allowPublicKeyRetrieval=true&useSSL=false',
        'PLATFORM_DB_USER': 'open_agent', 'PLATFORM_DB_PASSWORD': values['platform_db'],
        'PLATFORM_OIDC_ISSUER': identity['issuer'], 'PLATFORM_OIDC_JWKS': identity['issuer'] + '/.well-known/jwks',
        'PLATFORM_OIDC_AUDIENCE': identity['client_id'],
        'PLATFORM_S3_ENDPOINT': storage['s3_endpoint'], 'PLATFORM_S3_BUCKET': storage['s3_bucket'],
        'PLATFORM_S3_ACCESS_KEY': values['s3_app_key'], 'PLATFORM_S3_SECRET_KEY': values['s3_app_secret'],
        'MODEL_CREDENTIAL_ACTIVE_KEY': 'v1', 'MODEL_CREDENTIAL_KEYS': json.dumps({'v1': api_secrets['model_encryption_key']}),
        'PLATFORM_RUN_ADMISSION_ENABLED': 'false', 'LANGSMITH_TRACING': 'false',
        'PLATFORM_SERVICE_KEYS': json.dumps({
            'demo-worker': {'role': 'worker', 'secret': api_secrets['worker_key']},
            'demo-dispatcher': {'role': 'dispatcher', 'secret': api_secrets['dispatcher_key']},
        }),
    }
    private_file(ROOT / 'api.env', ''.join(key + '=' + value + '\n' for key, value in environment.items()), replace=True)
    ensure_container('open-agent-control-api', JRE, [
        '--memory', '896m', '--memory-swap', '896m', '--cpus', '1.5', '--user', '65531:65531',
        '--read-only', '--tmpfs', '/tmp:rw,nosuid,noexec,size=64m,uid=65531,gid=65531',
        '--env-file', str(ROOT / 'api.env'), '--entrypoint', 'java',
        '-v', f'{jar}:/app/platform.jar:ro',
    ], ['-Xms64m', '-Xmx448m', '-XX:MaxMetaspaceSize=192m', '-XX:MaxDirectMemorySize=96m',
        '-XX:+ExitOnOutOfMemoryError', '-jar', '/app/platform.jar'], replace_owned=True)
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
              'forged_identity_rejected': True, 'admission_enabled': False, 'public_port': False}
    private_file(ROOT / 'api-deployment.json', json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    with installation_lock():
        main()
