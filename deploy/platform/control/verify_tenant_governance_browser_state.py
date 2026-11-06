"""Verify persistent results after the documented browser governance exercise.

This script does not operate the browser or mutate memberships. It validates the
owned '治理验收 20261106' tenant created through the UI, using normal JWT APIs.
"""
from datetime import datetime, timezone
import json
from demo_session import DemoSession
from provision_data import ROOT, private_file
from verify_task_outputs import raw


def main():
    session = DemoSession()
    session.api = 'https://120.26.81.83'
    tenants = [row for row in session.call('/platform/tenants')['tenants']
               if row['displayName'] == '治理验收 20261106']
    assert len(tenants) == 1
    tenant = tenants[0]
    assert tenant['status'] == 'active' and tenant['revision'] == 2
    path = '/platform/tenants/' + tenant['tenantId']
    root = path + '/environments/test'
    roles = session.call(path + '/memberships')
    assert len(roles) == 1 and roles[0]['environment'] == 'test' and roles[0]['role'] == 'tenant_admin'
    members = session.call(root + '/members')
    assert len(members) == 1 and members[0]['uid'] == session.uid and members[0]['role'] == 'tenant_admin'
    quota = session.call(root + '/quotas')
    assert [quota[key] for key in ('maxConcurrentRuns', 'maxQueuedRuns', 'maxSandboxes', 'revision')] == [2, 10, 2, 1]
    assert all(value == 0 for value in quota['usage'].values())
    entries = list(reversed(session.call(root + '/audit')))
    assert [row['action'] for row in entries] == [
        'tenant.created', 'tenant.quota_changed', 'tenant.member_changed',
        'tenant.member_changed', 'tenant.member_removed', 'tenant.status_changed', 'tenant.status_changed']
    assert all(row['actorUid'] == session.uid for row in entries)
    before = json.loads(entries[1]['beforeValue'])
    after = json.loads(entries[1]['afterValue'])
    assert [before[key] for key in ('maxConcurrentRuns', 'maxQueuedRuns', 'maxSandboxes')] == [4, 32, 4]
    assert [after[key] for key in ('maxConcurrentRuns', 'maxQueuedRuns', 'maxSandboxes')] == [2, 10, 2]
    assert json.loads(entries[2]['afterValue'])['role'] == 'developer'
    assert json.loads(entries[3]['beforeValue'])['role'] == 'developer'
    assert json.loads(entries[3]['afterValue'])['role'] == 'viewer'
    assert json.loads(entries[4]['beforeValue'])['role'] == 'viewer' and entries[4]['afterValue'] is None
    assert json.loads(entries[5]['afterValue'])['status'] == 'suspended'
    assert json.loads(entries[6]['afterValue'])['status'] == 'active'
    for resource in ('quotas', 'members', 'audit'):
        raw(session, path + '/environments/prod/' + resource, 404)
    assert session.call(root + '/agent-deployments') == []
    assert session.call(root + '/runs')['items'] == []
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'public_https': True, 'read_only_verifier': True,
              'tenant': tenant['tenantId'], 'browser_created_tenant': True, 'expected_audit_actions': 7,
              'quota_before_after_verified': True, 'member_developer_viewer_removed_verified': True,
              'sole_admin_preserved': True, 'suspended_then_active_verified': True,
              'only_test_granted': True, 'production_quota_members_audit_denied': True,
              'no_deployments_or_tasks': True}
    private_file(ROOT / 'tenant-governance-browser-state.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
