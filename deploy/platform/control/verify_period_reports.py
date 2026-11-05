"""Read-only, per-row database cross-check of the four live natural-period reports.

No model/task submission, schema/data edits, or browser-download claim. Abort if
tasks are active: this verifier must not mistake a moving dataset for a mismatch.
"""
from datetime import datetime, timedelta, timezone
import calendar
import json
import math
import re

from demo_session import DemoSession
from provision_data import ROOT, DATABASE, command, private_file


def sql(query):
    return command('docker', 'exec', '-i', 'open-agent-control-mysql', 'sh', '-c',
        'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -N -uroot ' + DATABASE,
        input="SET time_zone='+00:00';\n" + query).stdout


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


def duration(start, finish):
    return (instant(finish)-instant(start)).total_seconds()*1000 if start and finish else None


def numeric(actual, expected):
    assert (actual is None) == (expected is None), 'period_null_semantics_mismatch'
    if expected is not None:
        assert math.isclose(float(actual), expected, rel_tol=1e-9, abs_tol=.001), 'period_numeric_mismatch'


def mean(values):
    return sum(values)/len(values) if values else None


def p95(values):
    return sorted(values)[math.ceil(len(values)*.95)-1] if values else None


def main():
    session = DemoSession()
    assert re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', session.tenant)
    scope = "tenant_id='" + session.tenant + "' AND environment='test'"
    def idle():
        assert int(sql("SELECT COUNT(*) FROM oa_agent_run WHERE " + scope +
            " AND state NOT IN ('succeeded','failed','cancelled','timed_out');").strip()) == 0, 'period_dataset_active'
    idle()
    reports = {}
    for period in ['this_week', 'last_week', 'this_month', 'last_month']:
        report = session.call(session.management+'/observation-statistics?period='+period)
        assert report['period'] == period and report['timezone'] == 'Asia/Shanghai'
        start, end, through = [instant(report[key]) for key in ['start','end','asOf']]
        zone = timezone(timedelta(hours=8))
        local_start, local_end = start.astimezone(zone), end.astimezone(zone)
        assert local_start.hour == local_end.hour == 0 and start < through <= end
        if 'week' in period:
            assert local_start.weekday() == 0 and end-start == timedelta(days=7)
        else:
            assert local_start.day == local_end.day == 1
            assert (end-start).days == calendar.monthrange(local_start.year,local_start.month)[1]
        bounds = [value.strftime('%Y-%m-%d %H:%M:%S.%f') for value in [start,through]]
        def window(column): return f"{column}>='{bounds[0]}' AND {column}<'{bounds[1]}'"
        def stamp(column): return f"DATE_FORMAT({column},'%Y-%m-%dT%H:%i:%s.%fZ')"
        tasks = [json.loads(line) for line in sql("SELECT JSON_OBJECT('created',"+stamp('created_at')+
            ",'started',"+stamp('started_at')+",'finished',"+stamp('finished_at')+",'state',state) FROM oa_agent_run WHERE "+scope+' AND '+window('created_at')+';').splitlines()]
        models = [json.loads(line) for line in sql("SELECT JSON_OBJECT('ended',"+stamp('ended_at')+
            ",'input',input_tokens,'output',output_tokens,'cache',cache_read_tokens,'source',usage_source,'ms',duration_ms) FROM oa_run_step WHERE "+scope+" AND kind='model' AND "+window('ended_at')+';').splitlines()]
        totals, usage = report['tasks'], report['usage']
        assert totals['tasks'] == len(tasks) and usage['modelCalls'] == len(models)
        assert totals['finishedTasks'] == sum(row['finished'] is not None for row in tasks)
        for state, key in [('succeeded','succeededTasks'),('failed','failedTasks'),('cancelled','cancelledTasks'),('timed_out','timedOutTasks')]:
            assert totals[key] == sum(row['state'] == state for row in tasks)
        # Derive each task's wall-clock duration independently; never add child spans.
        task_ms = [duration(row['started'],row['finished']) for row in tasks if row['started'] and row['finished']]
        model_ms = [row['ms'] for row in models if row['ms'] is not None]
        numeric(totals['meanTaskMs'],mean(task_ms)); numeric(totals['p95TaskMs'],p95(task_ms))
        numeric(usage['meanModelMs'],mean(model_ms)); numeric(usage['p95ModelMs'],p95(model_ms))
        expected_dates = [(local_start+timedelta(days=i)).date().isoformat() for i in range((end-start).days)]
        assert [row['date'] for row in report['daily']] == expected_dates
        for day in [*report['daily'], usage]:
            rows = [row for row in models if 'date' not in day or instant(row['ended']).astimezone(zone).date().isoformat() == day['date']]
            cached = [row for row in rows if row['cache'] is not None]
            for source,key in [('input','inputTokens'),('output','outputTokens')]:
                assert day[key] == sum(row[source] or 0 for row in rows)
            assert day['modelCalls'] == len(rows)
            assert day['unknownModelCalls'] == sum(row['source'] == 'unknown' for row in rows)
            assert day['cacheReportedModelCalls'] == len(cached)
            assert day['unknownCacheModelCalls'] == len(rows)-len(cached)
            numeric(day['cacheReadTokens'],sum(row['cache'] for row in cached) if cached else None)
            numeric(day['uncachedInputTokens'],sum(row['input']-row['cache'] for row in cached) if cached else None)
            if 'date' in day:
                assert day['tasks'] == sum(instant(row['created']).astimezone(zone).date().isoformat() == day['date'] for row in tasks)
        idle()
        reports[period] = {'start':report['start'],'end':report['end'],'asOf':report['asOf'],
            'days':len(report['daily']),'task_rows':len(tasks),'model_rows':len(models),
            'mean_task_ms':totals['meanTaskMs'],'p95_task_ms':totals['p95TaskMs'],
            'input_tokens':usage['inputTokens'],'cache_tokens':usage['cacheReadTokens'],'new_tokens':usage['uncachedInputTokens']}
    assert reports['last_week']['end'] == reports['this_week']['start']
    assert reports['last_month']['end'] == reports['this_month']['start']
    evidence = {'checked_at':datetime.now(timezone.utc).isoformat(),'periods':reports,
        'per_row_database_recomputed':True,'wall_clock_and_nearest_rank_p95_verified':True,
        'daily_timezone_and_unknown_cache_verified':True,'model_or_tool_replayed':False,
        'browser_csv_download_verified':False,'scope':'Live idle demo dataset; calendar/large-dataset fixtures are separate tests.'}
    private_file(ROOT/'period-reports-verification.json',json.dumps(evidence,indent=2)+'\n',replace=True)
    print(json.dumps(evidence),flush=True)


if __name__ == '__main__':
    main()
