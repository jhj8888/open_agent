"""Verify the owned public console using trusted TLS and normal demo OIDC tokens."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import ssl
import urllib.error
import urllib.request

from demo_session import DemoSession
from provision_data import ROOT, private_file

ORIGIN = 'https://120.26.81.83'


def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(route, headers=None, expected=200, method='GET', body=None):
        request = urllib.request.Request(ORIGIN + route, headers={'Content-Type': 'application/json', **(headers or {})},
                                         method=method, data=json.dumps(body).encode() if body is not None else None)
        try:
            response = opener.open(request, timeout=30)
        except urllib.error.HTTPError as failure:
            response = failure
        with response:
            if response.status != expected:
                raise RuntimeError('Unexpected public route status: ' + route + ': ' + str(response.status))
            return response.read(), dict(response.headers.items())

    deployment = json.loads(Path('/opt/open-agent/console/deployment.json').read_text())
    for name, digest in deployment['files'].items():
        content, headers = get('/open-agent/' + name)
        assert hashlib.sha256(content).hexdigest() == digest
        assert headers.get('Cache-Control') == 'no-store'
        assert "frame-ancestors 'none'" in headers.get('Content-Security-Policy', '')
    get('/internal/platform/reconcile/runs/test/lookup', expected=404)
    get('/platform/tenants', expected=401)
    get('/platform/tenants', {'X-User-Id': 'demo', 'X-Tenant-Id': 'space-1',
                              'X-Platform-Service-Key': 'forged'}, expected=401)
    get('/platform/profile', expected=401, method='POST')
    get('/platform/profile', {'X-User-Id': 'forged'}, expected=401, method='POST')
    discovery = json.loads(get('/identity/.well-known/openid-configuration')[0])
    assert discovery['issuer'] == ORIGIN + '/identity'
    assert 'S256' in discovery['code_challenge_methods_supported']
    session = DemoSession()
    tenants = json.loads(get('/platform/tenants', session.auth)[0])
    assert tenants['code'] == 0
    assert any(t['tenantId'] == session.tenant for t in tenants['data']['tenants'])
    profile = json.loads(get('/platform/profile', session.auth, method='POST',
                            body={'uid': 'forged', 'role': 'tenant_admin', 'tenantId': 'other'})[0])
    assert profile['code'] == 0 and profile['data']['uid'] == session.uid
    repeated = json.loads(get('/platform/profile', session.auth, method='POST')[0])
    assert repeated['data'] == profile['data']
    assert json.loads(get('/platform/tenants', session.auth)[0])['data'] == tenants['data']
    deployments = json.loads(get(session.management + '/agent-deployments', session.auth)[0])['data']
    usage_verified = 0
    for agent in deployments:
        runs = json.loads(get('/api/v1/agent-deployments/' + agent['deployment_id'] + '/runs', session.auth)[0])['data']
        for run in runs:
            assert 'usage_json' not in run
            if run.get('usage') is not None:
                detail = json.loads(get('/api/v1/runs/' + run['run_id'], session.auth)[0])['data']
                assert run['usage'] == detail['usage']
                usage_verified += 1
    assert usage_verified > 0
    context = ssl.create_default_context()
    with socket.create_connection(('120.26.81.83', 443), timeout=15) as connection:
        with context.wrap_socket(connection, server_hostname='120.26.81.83') as secure:
            certificate = secure.getpeercert()
            fingerprint = hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest()
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'origin': ORIGIN,
              'console_archive_sha256': deployment['archive_sha256'], 'asset_digests_verified': len(deployment['files']),
              'trusted_tls': True, 'certificate_sha256': fingerprint,
              'certificate_expires': certificate['notAfter'], 'issuer': discovery['issuer'],
              'pkce_s256': True, 'unauthenticated_rejected': True, 'forged_headers_rejected': True,
              'internal_route_denied': True, 'authenticated_public_api': True,
              'list_detail_usage_matches': usage_verified, 'static_csp': True,
              'profile_verified_subject_only': True, 'profile_idempotent': True,
              'profile_does_not_change_tenants': True,
              'browser_verification': 'separate manual browser evidence required'}
    private_file(ROOT / 'public-console-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
