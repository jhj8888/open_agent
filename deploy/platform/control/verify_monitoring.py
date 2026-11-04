"""Verify public monitoring authorization, complete SQL windows, and private collectors.

Uses existing synthetic demo identities; never creates tasks or calls a model.
Only the owned node exporter is stopped for a bounded failure/recovery check.
"""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from uuid import uuid4

from demo_session import DemoSession
from provision_data import ROOT, NETWORK, DATABASE, command, private_file

MONITOR = Path('/opt/open-agent/monitor')


def get(url, headers=None, method='GET'):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, headers=headers or {}, method=method), timeout=8) as response:
            return response.status, response.read(1024 * 1024).decode()
    except urllib.error.HTTPError as error:
        return error.code, ''


def denied(session, path, expected=404):
    try:
        session.call(path)
    except RuntimeError as error:
        assert str(error) == 'Demo API response status=' + str(expected)
    else:
        raise AssertionError('Monitoring authorization unexpectedly permitted a request')


def compare_window(session, tenant, environment, window):
    data = session.call(f'/platform/tenants/{tenant}/environments/{environment}/statistics?window={window}')
    assert re.fullmatch(r'[A-Za-z0-9-]+', tenant)
    assert environment in ('test', 'prod')
    start = datetime.fromisoformat(data['windowStart'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(data['windowEnd'].replace('Z', '+00:00'))
    assert (end-start).total_seconds() == {'1h': 3600, '24h': 86400, '7d': 604800}[window]
    since, until = (value.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f') for value in (start, end))
    sql = f"""SELECT JSON_OBJECT('total',COUNT(*),'reportedUsageRuns',COUNT(usage_json),
      'totalTokens',SUM(CAST(JSON_UNQUOTE(JSON_EXTRACT(usage_json,'$.total_tokens')) AS DECIMAL(30,0))))
      FROM oa_agent_run WHERE tenant_id='{tenant}' AND environment='{environment}'
        AND created_at>='{since}' AND created_at<'{until}';"""
    raw = command('docker','exec','-i','open-agent-control-mysql','sh','-c',
        'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -N -uroot '+DATABASE,input=sql).stdout.strip()
    expected = json.loads(raw)
    for key, value in expected.items():
        assert data['totals'][key] == value, key
    assert sum(row['count'] for row in data['states']) == expected['total']
    return {'tenant': tenant, 'environment': environment, 'window': window,
            'total': expected['total'], 'reported_usage_runs': expected['reportedUsageRuns'],
            'sql_window_matches': True}


def main():
    installation = json.loads((MONITOR/'deployment.json').read_text())
    assert installation['owner'] == 'open_agent.monitor.demo.v1'
    admin = DemoSession()
    secret = json.loads((ROOT/'member-secrets.json').read_text())
    member = DemoSession(secret['username'], secret['password'], require_tenant=False)
    admin.api = member.api = 'https://120.26.81.83'
    tenant = member.call('/platform/tenants')['tenants'][0]['tenantId']
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'public_https': True,
              'windows': [compare_window(admin, admin.tenant, env, window)
                          for env in ('test','prod') for window in ('1h','24h','7d')]}
    report['windows'].append(compare_window(member, tenant, 'test', '24h'))
    denied(member, admin.management+'/statistics')
    denied(member, f'/platform/tenants/{tenant}/environments/prod/statistics')
    denied(admin, f'/platform/tenants/{tenant}/environments/test/statistics')
    denied(member, '/platform/operations/host-metrics')
    report['tenant_environment_and_host_denials'] = 4

    keys = json.loads((ROOT/'api-secrets.json').read_text())
    endpoint = 'http://127.0.0.1:18082/internal/platform/metrics'
    observer = {'X-Platform-Key-Id':'demo-observer', 'X-Platform-Service-Key':keys['observer_key']}
    code, body = get(endpoint, observer)
    assert code == 200
    samples = [row for row in body.splitlines() if row and not row.startswith('#')]
    assert len(samples) == 32
    assert 'open_agent_metrics_collection_success 1.0' in samples
    for row in samples:
        assert re.fullmatch(r'open_agent_[a-z_]+(?:\{(?:environment="(?:test|prod)"|state="(?:queued|running|cancel_requested|succeeded|failed|timed_out|cancelled)"|phase="(?:reserved|creating|created|cleanup_pending)")(?:,(?:environment="(?:test|prod)"|state="(?:queued|running|cancel_requested|succeeded|failed|timed_out|cancelled)"|phase="(?:reserved|creating|created|cleanup_pending)"))*\})? [0-9.eE+-]+', row), 'Unexpected metric name/labels/value'
        assert math.isfinite(float(row.rsplit(' ', 1)[1]))
    assert get(endpoint)[0] == 401
    worker = {'X-Platform-Key-Id':'demo-worker','X-Platform-Service-Key':keys['worker_key']}
    assert get(endpoint, worker)[0] == 403
    assert get(endpoint, observer, 'POST')[0] == 403
    assert get('https://120.26.81.83/internal/platform/metrics', observer)[0] == 404
    report['metrics'] = {'finite_samples':len(samples),'bounded_labels':True,
                         'private_only':True,'worker_and_mutating_observer_denied':True}

    host = admin.call('/platform/operations/host-metrics')
    assert host['available'], host.get('reason')
    for name in ('nodeUp','apiUp','metricsHealthy','supervisorUp','temporalUp','ingressUp'):
        assert host['values'][name] == 1, name
    for name in ('cpuPercent','memoryPercent','diskPercent'):
        assert 0 <= host['values'][name] <= 100, name
    report['host'] = host
    prometheus = installation['prometheus_url']
    status, content = get(prometheus+'/api/v1/targets')
    targets = json.loads(content)['data']['activeTargets']
    assert status == 200 and len(targets) == 2 and all(row['health']=='up' for row in targets)
    status, content = get(prometheus+'/api/v1/rules')
    rules = json.loads(content)['data']['groups'][0]['rules']
    assert status == 200 and len(rules) == 7 and all(row['health']=='ok' for row in rules)
    report['alerts'] = {'loaded':len(rules),'healthy':True,
                        'behavior_verified':installation['alert_behavior_verified']}

    network = json.loads(command('docker','network','inspect', NETWORK).stdout)[0]
    assert network['Labels']['io.open-agent.owner'] == 'open_agent.control.demo.v1'
    address = prometheus.split('//')[1].split(':')[0]
    sandbox = json.loads(Path('/opt/open-agent/sandbox/deployment.json').read_text())
    name = 'oa-verify-monitor-'+uuid4().hex[:12]
    probe = ('import socket,sys\ns=socket.socket();s.settimeout(2)\n'
             'try:s.connect(('+repr(address)+',19090))\n'
             'except TimeoutError:print("denied");sys.exit(0)\n'
             'except OSError:sys.exit(2)\n'
             'sys.exit(1)\n')
    def drops():
        rows = command('iptables','-L','INPUT','-n','-v','-x').stdout.splitlines()
        return sum(int(row.split()[0]) for row in rows if 'open-agent-monitor-v1' in row)
    before = drops()
    try:
        result = command('docker','run','--rm','--pull=never','--name',name,
            '--label','io.open-agent.verification=monitor','--network',NETWORK,
            '--read-only','--cap-drop=ALL','--security-opt','no-new-privileges',
            '--user','1000:1000','--memory','128m','--pids-limit','32',
            '--entrypoint','python3',sandbox['runtime_image'],'-c',probe,timeout=12)
        assert result.stdout.strip() == 'denied' and drops() > before
    finally:
        command('docker','rm','--force',name,check=False)
    report['other_bridge_access_denied'] = True

    unit = Path('/etc/systemd/system/open-agent-node-exporter.service')
    assert 'open_agent.monitor.demo.v1' in unit.read_text()
    try:
        command('systemctl','stop','open-agent-node-exporter')
        for _ in range(22):
            unavailable = admin.call('/platform/operations/host-metrics')
            if not unavailable['available']: break
            time.sleep(1)
        else: raise AssertionError('Stopped source continued to appear healthy')
        report['stopped_source_withheld'] = True
    finally:
        command('systemctl','start','open-agent-node-exporter')
    for _ in range(22):
        recovered = admin.call('/platform/operations/host-metrics')
        if recovered['available']: break
        time.sleep(1)
    else: raise AssertionError('Metrics source did not recover')
    report['source_recovered'] = True
    private_file(ROOT/'monitoring-verification.json',json.dumps(report,indent=2)+'\n',replace=True)
    print(json.dumps(report),flush=True)


if __name__ == '__main__': main()
