"""Verify official Casdoor authorization-code/PKCE endpoints against the real API.

The seeded demo user is explicitly enrolled once; an existing bootstrap never
re-enables a revoked user or grants administrator status again. No tokens are saved.
"""

import base64
import hashlib
import http.cookiejar
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from provision_data import ROOT, DATABASE, command, container_address, installation_lock, private_file


def main():
    identity = json.loads((ROOT / 'identity-secrets.json').read_text())
    metadata = json.loads((ROOT / 'identity-deployment.json').read_text())
    endpoint = 'http://' + container_address('open-agent-control-identity') + ':8000'
    api = 'http://' + container_address('open-agent-control-api') + ':18082'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(url, body=None, headers=None):
        request_headers = {'Accept': 'application/json', **(headers or {})}
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         headers=request_headers)
        with opener.open(request, timeout=15) as response:
            return json.load(response)

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    query = urllib.parse.urlencode({'clientId': metadata['client_id'], 'responseType': 'code',
                                   'redirectUri': 'http://127.0.0.1:18080/callback', 'scope': 'openid profile',
                                   'state': secrets.token_urlsafe(24), 'nonce': secrets.token_urlsafe(24),
                                   'code_challenge_method': 'S256', 'code_challenge': challenge})
    login = call(endpoint + '/api/login?' + query, {
        'application': 'open-agent', 'organization': 'open-agent', 'username': 'demo',
        'password': identity['demo_password'], 'type': 'code', 'signinMethod': 'Password',
    })
    if login.get('status') != 'ok' or not isinstance(login.get('data'), str) or not login['data']:
        raise RuntimeError('Casdoor authorization failed (code=' + str(login.get('code')) + ')')
    token_request = {'grant_type': 'authorization_code', 'client_id': metadata['client_id'],
                     'code': login['data'], 'code_verifier': verifier,
                     'redirect_uri': 'http://127.0.0.1:18080/callback'}
    # A PKCE browser client must work without putting a client secret in the browser.
    tokens = call(endpoint + '/api/login/oauth/access_token', token_request)
    token = tokens.get('access_token')
    if not isinstance(token, str) or len(token.split('.')) != 3:
        raise RuntimeError('Casdoor did not return a JWT access token')
    auth = {'Authorization': 'Bearer ' + token}
    listing = call(api + '/platform/tenants', headers=auth)
    if listing.get('code') != 0 or not isinstance(listing.get('data', {}).get('tenants'), list):
        raise RuntimeError('API did not accept the actual OIDC token')
    # Subject is inspected only after the real Spring JWT verifier accepted this exact token.
    claims = json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))
    uid = claims.get('sub', '')
    if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', uid):
        raise RuntimeError('Unexpected verified demo subject')
    marker = ROOT / 'demo-user.json'
    if not marker.exists():
        sql = f"""START TRANSACTION;
INSERT IGNORE INTO user_info (uid,username,nickname,account_status,deleted) VALUES ('{uid}','demo','Open Agent Demo',1,0);
INSERT IGNORE INTO oa_platform_admin (uid) VALUES ('{uid}');
COMMIT;
"""
        command('docker', 'exec', '-i', 'open-agent-control-mysql', 'sh', '-c',
                'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -uroot ' + DATABASE, input=sql)
        private_file(marker, json.dumps({'uid': uid, 'identity': 'open-agent/demo'}, indent=2) + '\n')
    elif json.loads(marker.read_text())['uid'] != uid:
        raise RuntimeError('Demo identity changed; existing account not modified')
    listing = call(api + '/platform/tenants', headers=auth)
    if not listing['data'].get('platformAdmin'):
        raise RuntimeError('Demo user has no active administrator grant')
    owned = [tenant for tenant in listing['data']['tenants'] if tenant['displayName'] == '演示租户']
    if len(owned) > 1:
        raise RuntimeError('Ambiguous demo tenant')
    if not owned:
        created = call(api + '/platform/tenants', {'displayName': '演示租户', 'ownerUid': uid}, auth)
        if created.get('code') != 0:
            raise RuntimeError('Demo tenant creation failed')
        tenant_id = created['data']['tenantId']
    else:
        tenant_id = owned[0]['tenantId']
    membership = call(api + '/platform/tenants/' + tenant_id + '/memberships', headers=auth)
    if not any(item['environment'] == 'test' and item['role'] == 'tenant_admin' for item in membership['data']):
        raise RuntimeError('Test tenant authorization was not persisted')
    # Replay must not issue a second token for the same authorization code.
    try:
        replay = call(endpoint + '/api/login/oauth/access_token', token_request)
        if replay.get('access_token'):
            raise RuntimeError('Authorization code replay was accepted')
    except urllib.error.HTTPError as error:
        if error.code not in (400, 401):
            raise RuntimeError('Unexpected code replay response') from None
    keys_before = call(endpoint + '/.well-known/jwks')
    command('docker', 'restart', '-t', '30', 'open-agent-control-identity', timeout=60)
    for _ in range(60):
        try:
            keys_after = call(endpoint + '/.well-known/jwks')
            if keys_before != keys_after:
                raise RuntimeError('Identity signing keys changed during restart')
            break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2)
    else:
        raise RuntimeError('Identity service did not recover')
    command('docker', 'restart', '-t', '30', 'open-agent-control-mysql', timeout=60)
    command('docker', 'restart', '-t', '30', 'open-agent-control-api', timeout=60)
    for _ in range(90):
        try:
            listing = call(api + '/platform/tenants', headers=auth)
            if listing.get('code') != 0 or not any(t['tenantId'] == tenant_id for t in listing['data']['tenants']):
                raise RuntimeError('Tenant missing after API restart')
            break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2)
    else:
        raise RuntimeError('API did not recover after restart')
    result = {'oidc_authorization_code_pkce': True, 'browser_client_secret_required': False,
              'real_api_jwt_verified': True, 'authorization_code_replay_denied': True,
              'tenant_created_via_api': True, 'test_admin_membership_verified': True,
              'api_restart_tenant_persisted': True, 'database_restart_tenant_persisted': True,
              'identity_restart_key_persisted': True, 'admission_enabled': False,
              'browser_ui_verified': False}
    private_file(ROOT / 'identity-api-verification.json', json.dumps(result, indent=2) + '\n', replace=True)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    with installation_lock():
        main()
