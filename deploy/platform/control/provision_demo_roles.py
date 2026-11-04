"""Enroll separate demo governance/publisher roles through the existing APIs.

Uses Casdoor's official new-only init-data feature for this one demo identity.
Never resets an existing identity or reapplies grants after a completed setup.
"""

import json
import secrets
from demo_session import DemoSession
from enable_public_identity import keys
from provision_data import ROOT, DATABASE, command, installation_lock, private_file
from provision_identity import main as provision_identity


def main():
    secret_file = ROOT / 'publisher-secrets.json'
    if not secret_file.exists():
        private_file(secret_file, json.dumps({'username': 'publisher', 'password': secrets.token_urlsafe(36)}) + '\n')
    secret = json.loads(secret_file.read_text())
    marker = ROOT / 'publisher-user.json'
    if not marker.exists():
        before = keys()
        try:
            provision_identity(extra_users=[{'owner': 'open-agent', 'name': 'publisher', 'displayName': '演示发布人',
                                'type': 'normal-user', 'password': secret['password'], 'isAdmin': False,
                                'signupApplication': 'open-agent', 'isForbidden': False}])
            publisher = DemoSession('publisher', secret['password'], require_tenant=False)
            if keys() != before:
                raise RuntimeError('Identity signing keys changed')
            # One-time demo identity registration, no role or existing account update.
            sql = "INSERT IGNORE INTO user_info(uid,username,nickname,account_status,deleted) VALUES ('" + publisher.uid + "','publisher','Demo Publisher',1,0);"
            command('docker', 'exec', '-i', 'open-agent-control-mysql', 'sh', '-c',
                    'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -uroot ' + DATABASE, input=sql)
            private_file(marker, json.dumps({'uid': publisher.uid, 'identity': 'open-agent/publisher'}) + '\n')
        finally:
            provision_identity()  # Remove the password-bearing import from the running container.
    publisher_uid = json.loads(marker.read_text())['uid']
    admin = DemoSession()
    complete = ROOT / 'demo-roles.json'
    if not complete.exists():
        own = admin.call('/platform/tenants/' + admin.tenant + '/memberships')
        prod = [entry for entry in own if entry['environment'] == 'prod']
        if not prod:
            admin.call('/platform/tenants/' + admin.tenant + '/environments/prod/administrators/' + admin.uid,
                       {'role': 'tenant_admin', 'revision': None}, method='PUT')
        elif prod[0]['role'] != 'tenant_admin':
            raise RuntimeError('Existing demo production role differs')
        for environment in ('test', 'prod'):
            base = '/platform/tenants/' + admin.tenant + '/environments/' + environment
            members = admin.call(base + '/members')
            existing = [item for item in members if item['uid'] == publisher_uid]
            if not existing:
                admin.call(base + '/members/' + publisher_uid, {'role': 'operator', 'revision': None}, method='PUT')
            elif existing[0]['role'] != 'operator':
                raise RuntimeError('Existing publisher role differs')
        private_file(complete, json.dumps({'tenant_id': admin.tenant, 'administrator_uid': admin.uid,
                                          'publisher_uid': publisher_uid, 'separate_roles': True}) + '\n')
    publisher = DemoSession('publisher', secret['password'])
    if not any(entry['environment'] == 'prod' and entry['role'] == 'tenant_admin'
               for entry in admin.call('/platform/tenants/' + admin.tenant + '/memberships')):
        raise RuntimeError('Demo administrator permissions differ; no permissions restored')
    actual = publisher.call('/platform/tenants/' + admin.tenant + '/memberships')
    if sorted((value['environment'], value['role']) for value in actual) != [('prod', 'operator'), ('test', 'operator')]:
        raise RuntimeError('Publisher permissions differ; no permissions restored')
    print(json.dumps({'separate_roles': True, 'publisher_login_verified': True,
                      'production_administrator_enrolled': True, 'identity_admin_granted': False}), flush=True)


if __name__ == '__main__':
    with installation_lock():
        main()
