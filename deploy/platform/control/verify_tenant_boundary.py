"""Verify two real identities/tenants through the public TLS API using synthetic fixtures."""

from datetime import datetime, timezone
import json
from demo_session import DemoSession
from provision_data import ROOT, private_file


def main():
    admin = DemoSession()
    secret = json.loads((ROOT / 'member-secrets.json').read_text())
    member = DemoSession(secret['username'], secret['password'], require_tenant=False)
    admin.api = member.api = 'https://120.26.81.83'
    name = '权限验收租户'
    tenants = [row for row in admin.call('/platform/tenants')['tenants'] if row['displayName'] == name]
    if len(tenants) > 1:
        raise RuntimeError('Ambiguous verification tenant')
    tenant = tenants[0]['tenantId'] if tenants else admin.call('/platform/tenants', {
        'displayName': name, 'ownerUid': member.uid})['tenantId']
    own = '/platform/tenants/' + tenant + '/environments/test'
    visible = member.call('/platform/tenants')
    assert visible['platformAdmin'] is False
    assert [row['tenantId'] for row in visible['tenants']] == [tenant]
    grants = member.call('/platform/tenants/' + tenant + '/memberships')
    assert [(row['environment'],row['role']) for row in grants] == [('test','tenant_admin')]
    for resource in ('models','skill-releases','agent-deployments'):
        assert member.call(own + '/' + resource) == []
    fixture = json.loads((ROOT / 'demo-skill-agent.json').read_text())
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'public_https': True,
              'verification_tenant': tenant, 'independent_identity': True,
              'only_assigned_tenant_visible': True, 'only_test_role_granted': True, 'denials': []}

    def denied(session, case, route, body=None, method=None, headers=None):
        try:
            session.call(route, body, method=method, headers=headers)
        except RuntimeError as error:
            if str(error) != 'Demo API response status=404':
                raise
        else:
            raise RuntimeError('Expected isolation failure did not occur: ' + case)
        report['denials'].append({'case': case, 'status': 404})

    for resource in ('models','skill-releases','agent-deployments','members','audit'):
        denied(member,'foreign_'+resource,admin.management+'/'+resource)
    skill = '/skill-releases/' + fixture['skill_release_id']
    denied(member,'foreign_skill_manifest',admin.management+skill+'/manifest')
    denied(member,'foreign_skill_file',admin.management+skill+'/files/0')
    denied(member,'foreign_skill_id_in_own_scope',own+skill+'/files/0')
    run = '/api/v1/runs/5e3145f9-4559-4f9d-add5-1636eafbdd9b'
    denied(member,'foreign_run',run)
    denied(member,'foreign_run_events',run+'/events')
    denied(member,'forged_identity_header',admin.management+'/models',headers={'X-User-Id':admin.uid})
    denied(member,'own_production_without_grant',own.replace('/test','/prod')+'/agent-deployments')
    denied(admin,'platform_admin_is_not_tenant_business_member',own+'/agent-deployments')
    private_file(ROOT / 'tenant-boundary-verification.json',json.dumps(report,indent=2)+'\n',replace=True)
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    main()
