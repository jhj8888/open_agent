"""Compare public daily task metering with individual persisted model observations.

Read-only: no model calls, task replay, quota changes or synthetic production rows.
"""
from datetime import datetime, timedelta, timezone
import json
import re

from demo_session import DemoSession
from provision_data import ROOT, private_file
from verify_period_reports import sql
from verify_task_outputs import raw


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def main():
    session = DemoSession()
    session.api = 'https://120.26.81.83'
    total_before = sql('SELECT COUNT(*) FROM oa_agent_run;').strip()
    tenants = session.call('/platform/tenants')['tenants']
    checked = []
    for tenant in tenants:
        tenant_id = tenant['tenantId']
        assert re.fullmatch(r'[A-Za-z0-9_-]{1,80}', tenant_id)
        # Only the explicitly owned demo acceptance tenants, not every platform tenant.
        if tenant['displayName'] != '演示租户' and not tenant['displayName'].startswith(('治理验收 ', '任务暂停验收 ')):
            continue
        route = '/platform/tenants/' + tenant_id + '/environments/test/quotas'
        body, headers = raw(session, route)
        assert headers.get('Cache-Control') == 'no-store'
        quota = json.loads(body)['data']
        actual = quota['dailyTaskTokens']
        start, end, as_of = (instant(actual[field]) for field in ('start', 'end', 'asOf'))
        assert actual['timezone'] == 'Asia/Shanghai' and actual['asOf'] == quota['sampledAt']
        day = as_of.astimezone(timezone(timedelta(hours=8)))
        assert actual['date'] == day.date().isoformat()
        assert start == day.replace(hour=0, minute=0, second=0, microsecond=0)
        assert end == start + timedelta(days=1) and start <= as_of < end
        # Fetch individual rows including previous dates. Python independently selects and sums.
        rows = [json.loads(line) for line in sql(
            "SELECT JSON_OBJECT('started',DATE_FORMAT(started_at,'%Y-%m-%dT%H:%i:%s.%fZ'),"
            "'ended',DATE_FORMAT(ended_at,'%Y-%m-%dT%H:%i:%s.%fZ'),"
            "'source',usage_source,'input',input_tokens,'output',output_tokens,'cache',cache_read_tokens) "
            "FROM oa_run_step WHERE tenant_id='" + tenant_id + "' AND environment='test' AND kind='model';"
        ).splitlines()]
        completed = [row for row in rows if row['ended'] and start <= instant(row['ended']) < as_of]
        cached = [row for row in completed if row['cache'] is not None]
        expected = {
            'modelCalls': len(completed),
            'unknownModelCalls': sum(row['source'] == 'unknown' for row in completed),
            'unsettledModelCalls': sum(row['ended'] is None and instant(row['started']) < as_of for row in rows),
            'inputTokens': sum(row['input'] or 0 for row in completed),
            'outputTokens': sum(row['output'] or 0 for row in completed),
            'knownTotalTokens': sum((row['input'] or 0) + (row['output'] or 0) for row in completed),
            'cacheReadTokens': sum(row['cache'] for row in cached) if cached else None,
            'uncachedInputTokens': sum(row['input'] - row['cache'] for row in cached) if cached else None,
            'unknownCacheModelCalls': len(completed) - len(cached),
        }
        assert {key: actual[key] for key in expected} == expected, 'daily_metering_mismatch'
        assert quota['tokenBudgetEnforced'] is False
        # Detect changing observations rather than calling a racing comparison proof.
        again = session.call(route)['dailyTaskTokens']
        assert {key: again[key] for key in expected} == expected and again['date'] == actual['date'], 'dataset_changed'
        checked.append({'tenant': tenant_id, 'date': actual['date'], 'as_of': actual['asOf'], **expected})
    assert len(checked) >= 3, 'owned_acceptance_tenants_missing'
    secret = json.loads((ROOT / 'member-secrets.json').read_text())
    other = DemoSession(secret['username'], secret['password'], require_tenant=False)
    other.api = session.api
    raw(other, session.management + '/quotas', 404)
    assert total_before == sql('SELECT COUNT(*) FROM oa_agent_run;').strip()
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'read_only': True,
              'public_https': True, 'database_leaf_rows_match': True, 'cross_tenant_denied': True,
              'task_count_unchanged': True, 'token_budget_enforced': False, 'tenants': checked}
    private_file(ROOT / 'daily-task-tokens-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
