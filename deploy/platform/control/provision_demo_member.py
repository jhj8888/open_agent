"""Create a demo identity with no tenant grant, exercising normal profile enrollment."""

import json
import secrets
from datetime import datetime, timezone
from demo_session import DemoSession
from enable_public_identity import keys
from provision_data import ROOT, installation_lock, private_file
from provision_identity import main as provision_identity
from provision_api import main as provision_api


def main():
    secret_file = ROOT / 'member-secrets.json'
    if not secret_file.exists():
        private_file(secret_file, json.dumps({'username': 'member-demo', 'password': secrets.token_urlsafe(36)}) + '\n')
    secret = json.loads(secret_file.read_text())
    marker = ROOT / 'member-user.json'
    created = not marker.exists()
    if created:
        before = keys()
        try:
            provision_identity(extra_users=[{'owner': 'open-agent', 'name': secret['username'],
                'displayName': '新员工演示', 'type': 'normal-user', 'password': secret['password'],
                'isAdmin': False, 'signupApplication': 'open-agent', 'isForbidden': False}])
            if keys() != before:
                raise RuntimeError('Identity signing keys changed')
        finally:
            provision_identity()
        provision_api()
    member = DemoSession(secret['username'], secret['password'], require_tenant=False)
    original = member.call('/platform/tenants')
    if original != {'platformAdmin': False, 'tenants': []}:
        raise RuntimeError('This fixture has acquired grants; refusing to overwrite or remove them')
    profile = member.call('/platform/profile', method='POST')
    again = member.call('/platform/profile', {'uid': 'forged', 'role': 'tenant_admin', 'tenantId': 'space-1'})
    if profile != again or profile['uid'] != member.uid or member.call('/platform/tenants') != original:
        raise RuntimeError('Identity-only enrollment contract differs')
    private_file(marker, json.dumps({'uid': member.uid, 'identity': 'open-agent/member-demo'}) + '\n', replace=True)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'identity_created': created,
              'normal_oidc_login': True, 'verified_subject_only': True, 'profile_idempotent': True,
              'tenant_grants': 0, 'platform_admin': False, 'direct_user_sql_used': False}
    private_file(ROOT / 'member-enrollment-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    with installation_lock():
        main()
