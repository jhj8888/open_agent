"""Exercise bounded diagnostics against authorized demo models, not business tasks.

Makes at most six fixed model requests; stores only the public diagnostic report.
Credentials are obtained through normal OIDC and never printed or embedded.
"""
from datetime import datetime, timezone
import json
import re
import urllib.error
import urllib.request

from demo_session import DemoSession
from provision_data import ROOT, private_file
from verify_period_reports import sql


def post(session, path, allowed=(200,)):
    request = urllib.request.Request(session.api + path, data=b'{}', method='POST',
                                     headers={**session.auth, 'Content-Type': 'application/json'})
    try:
        with session.opener.open(request, timeout=75) as response:
            assert response.status in allowed
            assert response.headers.get('Cache-Control') == 'no-store'
            result = json.load(response)
            assert result['code'] == 0
            return result['data']
    except urllib.error.HTTPError as error:
        assert error.code in allowed, 'unexpected_probe_http_status_' + str(error.code)
        return None


def main():
    session = DemoSession()
    session.api = 'https://120.26.81.83'
    assert re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session.tenant)
    models = [m for m in session.call(session.management + '/models')
              if m['enabled'] and not m['revoked'] and m['supports_stream'] and m['supports_tools']
              and m['adapter'] == 'deepseek']
    assert models, 'configured_demo_model_required'
    # Prefer one connection per thinking configuration, without editing versions.
    selected = list({bool(m['thinking_enabled']): m for m in models}.values())[:2]
    other = DemoSession('member-demo', json.loads((ROOT / 'member-secrets.json').read_text())['password'], require_tenant=False)
    other.api = session.api
    before_runs = int(sql('SELECT COUNT(*) FROM oa_agent_run;').strip())
    reports = []
    for model in selected:
        identity = model['connection_id']
        assert re.fullmatch(r'[a-f0-9-]{36}', identity)
        route = session.management + '/models/' + identity + '/probe'
        post(other, route, (403, 404))
        result = post(session, route)
        assert result['ok'] and result['stream'] and result['toolsVerified'], 'live_probe_failed_' + str(result.get('error'))
        assert result['version'] == model['current_version']
        assert result['adapter'] == 'deepseek' and result['thinkingEnabled'] == bool(model['thinking_enabled'])
        assert [s['name'] for s in result['steps']] == ['stream', 'tool_call', 'tool_result']
        assert all(s['status'] == 'passed' and s['durationMs'] >= 0 for s in result['steps'])
        assert all(s['inputTokens'] is not None and s['outputTokens'] is not None for s in result['steps'])
        audit = json.loads(sql("SET NAMES utf8mb4; SELECT after_value FROM oa_tenant_audit WHERE tenant_id='" + session.tenant +
                              "' AND environment='test' AND action='model.probe.completed' AND subject_id='" + identity +
                              "' ORDER BY created_at DESC LIMIT 1;").strip())
        assert audit == result, 'probe_audit_differs'
        assert all(set(s) == {'name', 'status', 'durationMs', 'inputTokens', 'outputTokens', 'error'} for s in result['steps'])
        reports.append(result)
    assert int(sql('SELECT COUNT(*) FROM oa_agent_run;').strip()) == before_runs, 'probe_created_business_task'
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'public_https': True,
              'connections_checked': len(reports), 'reports': reports, 'audit_matches': True,
              'cross_tenant_denied': True, 'business_task_count_unchanged': True,
              'scope': 'authorized public demo endpoint; not internal offline acceptance'}
    private_file(ROOT / 'model-capability-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
