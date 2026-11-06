"""Read-only governance verification through public HTTPS against owned demo SQL.

No task execution, model requests, membership changes or synthetic audit insertion.
The dataset must remain idle while comparing cursor order and current occupancy.
"""
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlencode

from demo_session import DemoSession
from provision_data import ROOT, private_file
from verify_period_reports import sql
from verify_task_outputs import raw


def main():
    session = DemoSession()
    session.api = 'https://120.26.81.83'
    assert re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session.tenant)
    scope = "tenant_id='" + session.tenant + "' AND environment='test'"
    task_count = sql('SELECT COUNT(*) FROM oa_agent_run;').strip()

    def expected(action=''):
        assert not action or re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', action)
        condition = scope + (" AND action='" + action + "'" if action else '')
        return [json.loads(line) for line in sql(
            "SET NAMES utf8mb4; SELECT JSON_OBJECT('cursor',CAST(id AS CHAR),'actorUid',actor_uid,"
            "'action',action,'subjectId',subject_id,'beforeValue',before_value,'afterValue',after_value) "
            'FROM oa_tenant_audit WHERE ' + condition + ' ORDER BY id DESC;').splitlines()]

    def normalized(value):
        return json.loads(value) if isinstance(value, str) else value

    def pages(action=''):
        before, rows, count = None, [], 0
        while True:
            query = {'action': action}
            if before:
                query['beforeId'] = before
            body, headers = raw(session, session.management + '/audit?' + urlencode(query))
            assert headers.get('Cache-Control') == 'no-store'
            page = json.loads(body)['data']
            count += 1
            assert len(page) <= 100 and count <= 1000
            for row in page:
                assert isinstance(row['cursor'], str) and re.fullmatch(r'[0-9]+', row['cursor'])
                assert 'retainUntil' in row
            rows.extend(page)
            next_id = page[-1].get('nextBeforeId') if page else None
            if next_id is None:
                return rows, count
            assert len(page) == 100 and next_id == page[-1]['cursor'] and next_id != before
            before = next_id

    original = expected()
    assert original, 'audit_dataset_empty'
    rows, count = pages()
    assert [x['cursor'] for x in rows] == [x['cursor'] for x in original]
    for actual, stored in zip(rows, original):
        for key in ('actorUid', 'action', 'subjectId'):
            assert actual[key] == stored[key]
        for key in ('beforeValue', 'afterValue'):
            assert normalized(actual[key]) == normalized(stored[key])
    action = original[0]['action']
    filtered, _ = pages(action)
    assert [x['cursor'] for x in filtered] == [x['cursor'] for x in expected(action)]
    quota = session.call(session.management + '/quotas')
    assert quota['tokenBudgetEnforced'] is False and quota['sampledAt']
    for key, table, condition in [('queuedRuns', 'oa_agent_run', "state='queued'"),
                                   ('executionSlots', 'oa_run_attempt', "lease_state<>'released'"),
                                   ('sandboxes', 'oa_run_sandbox', "phase<>'released'")]:
        assert quota['usage'][key] == int(sql('SELECT COUNT(*) FROM ' + table + ' WHERE ' + scope + ' AND ' + condition + ';'))
    for query in ({'beforeId': '0'}, {'beforeId': '9223372036854775808'}, {'action': "bad'filter"}):
        raw(session, session.management + '/audit?' + urlencode(query), 400)
    secret = json.loads((ROOT / 'member-secrets.json').read_text())
    other = DemoSession(secret['username'], secret['password'], require_tenant=False)
    other.api = session.api
    for resource in ('quotas', 'audit', 'audit?beforeId=' + original[-1]['cursor']):
        raw(other, session.management + '/' + resource, 404)
    assert expected() == original, 'audit_dataset_changed_during_verification'
    assert task_count == sql('SELECT COUNT(*) FROM oa_agent_run;').strip()
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'read_only': True, 'public_https': True,
              'rows': len(rows), 'pages': count, 'page_size': 100, 'database_order_and_values_match': True,
              'action_filter_matches': True, 'invalid_queries_denied': True,
              'cross_tenant_audit_cursor_and_quotas_denied': True, 'occupancy_matches_database': True,
              'token_budget_enforcement': False, 'business_tasks_created': False}
    private_file(ROOT / 'tenant-governance-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
