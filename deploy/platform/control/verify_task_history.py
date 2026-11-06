"""Read-only public history pagination checked against the owned demo database.

No fixture tasks, model requests or permission changes. Requires an idle dataset;
HTTP errors never print authentication headers or task content.
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
    scope = "r.tenant_id='" + session.tenant + "' AND r.environment='test'"

    def idle():
        assert int(sql("SELECT COUNT(*) FROM oa_agent_run r WHERE " + scope +
                       " AND r.state IN ('queued','running','cancel_requested');").strip()) == 0, 'history_dataset_active'

    idle()
    expected = [json.loads(line) for line in sql(
        "SET NAMES utf8mb4; SELECT JSON_OBJECT('id',r.run_id,'state',r.state,'name',d.display_name) FROM oa_agent_run r "
        "JOIN oa_agent_deployment d ON d.deployment_id=r.deployment_id WHERE " + scope +
        " AND NOT EXISTS(SELECT 1 FROM oa_run_deletion x WHERE x.run_id=r.run_id) "
        "ORDER BY r.created_at DESC,r.run_id DESC;").splitlines()]
    assert len(expected) > 7, 'history_fixture_requires_multiple_pages'
    rows, before, pages, first_cursor = [], None, 0, None
    while True:
        query = {'limit': 7}
        if before:
            query['before'] = before
        body, headers = raw(session, session.management + '/runs?' + urlencode(query))
        assert headers.get('Cache-Control') == 'no-store'
        page = json.loads(body)['data']
        assert len(page['items']) <= 7
        rows.extend(page['items'])
        pages += 1
        if not page['hasMore']:
            assert page['next'] is None
            break
        assert page['items'] and page['next'] and page['next'] != before and pages < 100
        before = page['next']
        first_cursor = first_cursor or before
    assert [row['run_id'] for row in rows] == [row['id'] for row in expected]
    assert len({row['run_id'] for row in rows}) == len(rows)
    for row, original in zip(rows, expected):
        assert row['agent_name'] == original['name'] and row['state'] == original['state']
        detail = session.call('/api/v1/runs/' + row['run_id'])
        assert row['usage'] == detail['usage'] and row['deployment_id'] == detail['deployment_id']
        assert not {'input_json', 'snapshot_json', 'actor_id', 'usage_json'} & row.keys()
    filters = {}
    for label, states in {'active': ['queued', 'running', 'cancel_requested'], 'succeeded': ['succeeded'],
                          'failed': ['failed', 'timed_out'], 'cancelled': ['cancelled']}.items():
        page = session.call(session.management + '/runs?' + urlencode({'limit': 100, 'state': label}))
        assert [row['run_id'] for row in page['items']] == [row['id'] for row in expected if row['state'] in states]
        filters[label] = len(page['items'])
    sample = expected[-1]
    for term, matches in [(sample['id'], [sample['id']]), (sample['name'], [row['id'] for row in expected if sample['name'] in row['name']])]:
        found = session.call(session.management + '/runs?' + urlencode({'limit': 100, 'search': term}))
        assert [row['run_id'] for row in found['items']] == matches
    for query in [{'limit': 101}, {'limit': 0}, {'before': '*'}, {'state': 'invalid'},
                  {'limit': 7, 'before': first_cursor, 'state': 'failed'}]:
        raw(session, session.management + '/runs?' + urlencode(query), 422)
    raw(session, session.management.replace('/test', '/prod') + '/runs?' +
        urlencode({'limit': 7, 'before': first_cursor}), 422)
    other = DemoSession('member-demo', json.loads((ROOT / 'member-secrets.json').read_text())['password'], require_tenant=False)
    other.api = session.api
    raw(other, session.management + '/runs', 404)
    raw(other, session.management + '/runs?' + urlencode({'limit': 7, 'before': first_cursor}), 404)
    idle()
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'read_only': True, 'public_https': True,
              'rows': len(rows), 'pages': pages, 'page_size': 7, 'database_order_matches': True,
              'usage_and_deployment_match_details': True, 'filters': filters, 'search_verified': True,
              'invalid_cursor_and_queries_denied': True, 'cross_environment_cursor_denied': True,
              'cross_tenant_history_and_cursor_denied': True, 'task_execution_replayed': False}
    private_file(ROOT / 'task-history-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
