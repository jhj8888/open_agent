"""Read-only checks of retained model usage and task/period cache breakdowns."""
from datetime import datetime, timezone
import json

from demo_session import DemoSession
from provision_data import ROOT, private_file
from verify_task_outputs import raw


def main():
    session = DemoSession()
    previous = json.loads((ROOT / 'step-content-verification.json').read_text())
    run_id = previous['run_id']
    rows, after = [], 0
    pages = []
    while True:
        page = session.call(f'/api/v1/runs/{run_id}/observations?after={after}&limit=3')
        rows.extend(page['items'])
        pages.append(page['totals'])
        if not page['hasMore']:
            break
        assert page['next'] > after
        after = page['next']
    models = [row for row in rows if row['kind'] == 'model']
    known = [row for row in models if row['cache_read_tokens'] is not None]
    assert known and all(0 <= row['cache_read_tokens'] <= row['input_tokens'] for row in known)
    expected = {
        'cacheReadTokens': sum(row['cache_read_tokens'] for row in known),
        'uncachedInputTokens': sum(row['input_tokens'] - row['cache_read_tokens'] for row in known),
        'cacheReportedModelCalls': len(known),
        'unknownCacheModelCalls': len(models) - len(known),
    }
    assert all(all(totals[key] == value for key, value in expected.items()) for totals in pages)
    run = session.call(f'/api/v1/runs/{run_id}')
    assert sum(row['input_tokens'] or 0 for row in models) == run['usage']['prompt_tokens']
    periods = {}
    for period in ['this_week', 'last_week', 'this_month', 'last_month']:
        result = session.call(session.management + '/observation-statistics?period=' + period)
        usage, daily = result['usage'], result['daily']
        for field in ['cacheReadTokens', 'uncachedInputTokens']:
            reported = [day[field] for day in daily if day[field] is not None]
            assert usage[field] == (sum(reported) if reported else None)
        for field in ['cacheReportedModelCalls', 'unknownCacheModelCalls']:
            assert usage[field] == sum(day[field] for day in daily)
        assert usage['cacheReportedModelCalls'] + usage['unknownCacheModelCalls'] == usage['modelCalls']
        if not usage['unknownCacheModelCalls'] and usage['modelCalls']:
            assert usage['cacheReadTokens'] + usage['uncachedInputTokens'] == usage['inputTokens']
        periods[period] = {field:usage[field] for field in ['modelCalls', 'inputTokens', *expected]}
    # Use the pre-existing independent tenant account; never broaden permissions.
    other = DemoSession('member-demo', json.loads((ROOT / 'member-secrets.json').read_text())['password'],
                        require_tenant=False)
    raw(other, f'/api/v1/runs/{run_id}/observations?limit=3', 404)
    evidence = {'checked_at':datetime.now(timezone.utc).isoformat(), 'run_id':run_id,
        'steps':len(rows), 'pages_verified':len(pages), 'model_calls':len(models), 'task':expected,
        'periods':periods, 'task_total_unchanged':True, 'other_tenant_denied':True,
        'model_or_tool_replayed':False,
        'examples':[{'step_id':row['step_id'], 'input':row['input_tokens'], 'cached':row['cache_read_tokens'],
            'new':row['input_tokens']-row['cache_read_tokens'], 'output':row['output_tokens']} for row in known[:3]]}
    private_file(ROOT/'cache-tokens-verification.json', json.dumps(evidence,indent=2)+'\n', replace=True)
    print(json.dumps(evidence), flush=True)


if __name__ == '__main__':
    main()
