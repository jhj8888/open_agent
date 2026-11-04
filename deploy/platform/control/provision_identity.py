"""Install an isolated Casdoor identity service using its official init-data mechanism.

The private origin is for integration only. Public ingress and browser OIDC rollout
require a separately configured stable origin; no public admin port is published.
"""

import json
from pathlib import Path
import secrets
import time
import urllib.error
import urllib.request

from provision_data import ROOT, data_dir, ensure_container, initialize, private_file, container_address, installation_lock

IMAGE = 'm.daocloud.io/docker.io/casbin/casdoor@sha256:a63e7a78257c43c90b22a49bf93becca7007dc47b4f97bec763aa50bcc9c9ee9'
ORIGIN = 'http://open-agent-control-identity:8000'
CLIENT_ID = 'open-agent-console'


def main():
    values = initialize()
    path = ROOT / 'identity-secrets.json'
    if path.is_symlink():
        raise RuntimeError('Linked identity secrets are not allowed')
    if not path.exists():
        private_file(path, json.dumps({key: secrets.token_urlsafe(36) for key in (
            'admin_password', 'demo_password', 'client_secret')}, indent=2) + '\n')
    identity = json.loads(path.read_text())
    data_dir('identity', 1000, 1000)
    config = '\n'.join([
        'appname = casdoor', 'httpport = 8000', 'runmode = prod', 'copyrequestbody = true',
        'driverName = mysql',
        'dataSourceName = open_agent_identity:' + values['identity_db'] + '@tcp(open-agent-control-mysql:3306)/',
        'dbName = open_agent_identity', 'showSql = false', 'redisEndpoint =', 'socks5Proxy =',
        'origin = ' + ORIGIN, 'originFrontend = ' + ORIGIN,
        'staticBaseUrl = ' + ORIGIN + '/static', 'isDemoMode = false',
        'initAdminPassword = ' + identity['admin_password'],
        'defaultApplication = open-agent', 'defaultLanguage = zh', 'forceLanguage = zh',
        'showGithubCorner = false', 'enableErrorMask = true', 'enableGzip = true',
        'logConfig = {"adapter":"console","level":3}', 'logPostOnly = true',
        'initDataFile = /init_data.json', 'initDataNewOnly = false', 'initDataMerge = true',
        'initDataWatchInterval = 0', 'frontendBaseDir = /cc_0',
    ]) + '\n'
    private_file(ROOT / 'identity.conf', config, 1000, 1000)
    data = {
        'organizations': [
            {'owner': 'admin', 'name': 'built-in', 'isProfilePublic': False},
            {'owner': 'admin', 'name': 'open-agent', 'displayName': 'Open Agent',
             'passwordType': 'bcrypt', 'passwordOptions': ['AtLeast8'],
             'isProfilePublic': False, 'defaultApplication': 'open-agent',
             'defaultTokenFormat': 'JWT', 'languages': ['zh', 'en']},
        ],
        'applications': [
            {'owner': 'admin', 'name': 'app-built-in', 'enableSignUp': False,
             'providers': [], 'signinMethods': [{'name': 'Password', 'rule': 'All'}]},
            {'owner': 'admin', 'name': 'open-agent', 'displayName': 'Open Agent',
             'organization': 'open-agent', 'cert': 'cert-open-agent', 'enablePassword': True,
             'enableSignUp': False, 'providers': [],
             'signinMethods': [{'name': 'Password', 'displayName': 'Password', 'rule': 'All'}],
             'clientId': CLIENT_ID, 'clientSecret': identity['client_secret'],
             'grantTypes': ['authorization_code', 'refresh_token'],
             'redirectUris': ['http://127.0.0.1:18080/callback'], 'tokenFormat': 'JWT',
             'tokenFields': [], 'expireInHours': 1, 'failedSigninLimit': 5,
             'failedSigninFrozenTime': 15, 'cookieExpireInHours': 8},
        ],
        'users': [{'owner': 'open-agent', 'name': 'demo', 'displayName': '演示管理员',
                   'type': 'normal-user', 'password': identity['demo_password'], 'isAdmin': False,
                   'signupApplication': 'open-agent', 'isForbidden': False}],
        # Casdoor generates this installation's RSA key with its official certificate service.
        'certs': [{'owner': 'admin', 'name': 'cert-open-agent', 'displayName': 'Open Agent OIDC',
                   'scope': 'JWT', 'type': 'x509', 'cryptoAlgorithm': 'RS256', 'bitSize': 3072,
                   'expireInYears': 2}],
    }
    private_file(ROOT / 'identity-init.json', json.dumps(data, ensure_ascii=False, indent=2) + '\n', 1000, 1000)
    ensure_container('open-agent-control-identity', IMAGE, [
        '--memory', '384m', '--memory-swap', '384m', '--cpus', '1', '--user', '1000:1000',
        '--read-only', '--tmpfs', '/tmp:rw,nosuid,noexec,size=32m,uid=1000,gid=1000',
        '--tmpfs', '/logs:rw,nosuid,noexec,size=16m,uid=1000,gid=1000', '-e', 'GOMEMLIMIT=256MiB',
        '-v', f'{ROOT}/identity.conf:/conf/app.conf:ro',
        '-v', f'{ROOT}/identity-init.json:/init_data.json:ro',
        '-v', f'{ROOT}/identity:/files',
    ], [])
    endpoint = 'http://' + container_address('open-agent-control-identity') + ':8000'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(90):
        try:
            with opener.open(endpoint + '/.well-known/openid-configuration', timeout=3) as response:
                discovery = json.load(response)
            if discovery['issuer'] != ORIGIN or 'S256' not in discovery.get('code_challenge_methods_supported', []):
                raise RuntimeError('Unexpected issuer or PKCE discovery')
            jwks_path = discovery['jwks_uri'].removeprefix(ORIGIN)
            if not jwks_path.startswith('/'):
                raise RuntimeError('Unexpected identity signing-key URL')
            with opener.open(endpoint + jwks_path, timeout=3) as response:
                jwks = json.load(response)
            if not any(key.get('kty') == 'RSA' and key.get('n') and key.get('e') for key in jwks['keys']):
                raise RuntimeError('No RSA identity signing key')
            break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2)
    else:
        raise RuntimeError('Identity service did not become ready; inspect owned logs')
    result = {'issuer': ORIGIN, 'client_id': CLIENT_ID, 'pkce_s256': True,
              'image': IMAGE, 'public_port': False, 'browser_flow_verified': False}
    private_file(ROOT / 'identity-deployment.json', json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    with installation_lock():
        main()
