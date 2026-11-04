"""Exercise real publication, role boundaries, production HTTP runs and key rotation."""

from datetime import datetime, timezone
import json
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

from demo_session import DemoSession
from provision_data import ROOT, private_file


def main():
    configuration = json.loads(sys.stdin.read(65537))
    admin = DemoSession()
    publisher_secret = json.loads((ROOT / 'publisher-secrets.json').read_text())
    publisher = DemoSession('publisher', publisher_secret['password'])
    prod = '/platform/tenants/' + admin.tenant + '/environments/prod'
    path = ROOT / 'demo-production.json'
    saved = json.loads(path.read_text()) if path.exists() else {}

    def checkpoint():
        private_file(path, json.dumps(saved, indent=2) + '\n', replace=True)

    def denied(session, route, body=None, method=None, status=404):
        try:
            session.call(route, body, method=method)
        except RuntimeError as error:
            if str(error) == 'Demo API response status=' + str(status):
                return
            raise
        raise RuntimeError('Expected permission denial did not occur')

    models = admin.call(prod + '/models')
    owned = [value for value in models if value['display_name'] == 'DeepSeek 生产展示接口']
    if len(owned) > 1:
        raise RuntimeError('Ambiguous production model')
    if not owned:
        value = admin.call(prod + '/models', {
            'displayName': 'DeepSeek 生产展示接口', 'endpoint': configuration['base_url'], 'modelName': configuration['model'],
            'supportsStream': True, 'supportsTools': True, 'supportsUsage': True,
            'connectTimeoutSeconds': 10, 'readTimeoutSeconds': 90, 'totalTimeoutSeconds': 180,
            'apiKey': configuration['api_key'], 'expectedVersion': 0, 'revokePrevious': False,
        })
        model_id, model_version = value['connectionId'], value['version']
    else:
        model_id, model_version = owned[0]['connection_id'], owned[0]['current_version']
    saved['model_connection_id'], saved['model_version'] = model_id, model_version
    checkpoint()
    sources = {'model': json.loads((ROOT / 'demo-agent.json').read_text()),
               'skill': json.loads((ROOT / 'demo-skill-agent.json').read_text())}
    for kind, source in sources.items():
        evidence = json.loads((ROOT / (kind + '-run-verification.json')).read_text())
        body = {'sourceDeploymentId': source['deployment_id'], 'modelConnectionId': model_id,
                'modelVersion': model_version, 'verificationRunId': evidence['run_id']}
        denied(admin, prod + '/agent-deployments/promote', body)
        if kind + '_deployment_id' not in saved:
            target = publisher.call(prod + '/agent-deployments/promote', body)
            saved[kind + '_deployment_id'] = target['deployment_id']
            saved[kind + '_verification_run_id'] = evidence['run_id']
            checkpoint()
        target = admin.call('/api/v1/agent-deployments/' + saved[kind + '_deployment_id'])
        if target['environment'] != 'prod' or target['state'] != 'published':
            raise RuntimeError('Production deployment unavailable')
    route = prod + '/agent-deployments/' + saved['model_deployment_id']
    denied(publisher, route + '/credentials')
    admin.call(route + '/grants/' + admin.uid, method='PUT')
    issued = admin.call(route + '/credentials', {'displayName': 'production-contract-verification',
                       'canCreate': True, 'canRead': True, 'canCancel': True, 'validDays': 1})
    current_key = issued['keyId']
    origin = 'https://120.26.81.83'

    def public(path, token, body=None, headers=None, expected=200):
        request = urllib.request.Request(origin + path, data=json.dumps(body).encode() if body is not None else None,
                    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(headers or {})})
        try:
            response = admin.opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.status != expected:
                raise RuntimeError('Unexpected production public API status: ' + str(response.status))
            return json.load(response)['data'] if expected in (200, 202) else None

    try:
        body = {'input': {'message': '请用一句中文说明生产发布前验证固定 Skill 版本的价值。'}, 'timeoutSeconds': 180}
        key = 'production-demo-' + str(uuid4())
        accepted = public('/api/v1/agent-deployments/' + saved['model_deployment_id'] + '/runs', issued['token'], body,
                          {'Idempotency-Key': key}, expected=202)
        run_id = accepted['run_id']
        repeated = public('/api/v1/agent-deployments/' + saved['model_deployment_id'] + '/runs', issued['token'], body,
                          {'Idempotency-Key': key}, expected=202)
        assert repeated['run_id'] == run_id and repeated['reused']
        started = time.monotonic()
        execution = None
        service = json.loads((ROOT / 'api-secrets.json').read_text())
        while time.monotonic() - started < 210:
            status = public('/api/v1/runs/' + run_id, issued['token'])
            lookup = admin.request(admin.api + '/internal/platform/reconcile/runs/' + run_id + '/lookup',
                                   {'tenant': admin.tenant, 'environment': 'prod'},
                                   {'X-Platform-Key-Id': 'demo-supervisor', 'X-Platform-Service-Key': service['reconciler_key']})
            execution = lookup.get('execution')
            if status['state'] in ('succeeded', 'failed', 'timed_out', 'cancelled') and execution and execution['phase'] == 'released':
                break
            time.sleep(2)
        assert status['state'] == 'succeeded' and execution['leaseState'] == 'released'
        assert status['usage']['total_tokens'] > 0
        rotated = admin.call(route + '/credentials/' + current_key + '/rotate', method='POST')
        current_key = rotated['keyId']
        public('/api/v1/runs/' + run_id, issued['token'], expected=401)
        assert public('/api/v1/runs/' + run_id, rotated['token'])['run_id'] == run_id
        admin.call(route + '/credentials/' + current_key, method='DELETE')
        public('/api/v1/runs/' + run_id, rotated['token'], expected=401)
        skills = admin.call(prod + '/skill-releases')
        released_skill = sources['skill']['skill_release_id']
        assert any(s['release_id'] == released_skill and s['verified'] for s in skills)
        report = {'checked_at': datetime.now(timezone.utc).isoformat(), **saved,
                  'production_model_run_id': run_id, 'state': status['state'], 'usage': status['usage'],
                  'supervision_phase': execution['phase'], 'lease_state': execution['leaseState'],
                  'admin_cannot_publish': True, 'publisher_cannot_manage_credentials': True,
                  'skill_exact_evidence_promoted': True, 'http_idempotency': True,
                  'rotated_key_retains_run_access': True, 'old_and_revoked_keys_rejected': True,
                  'production_skill_execution': 'not tested: production requires Kata; demo is gVisor test only'}
        private_file(ROOT / 'promotion-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
        print(json.dumps(report), flush=True)
    finally:
        admin.call(route + '/credentials/' + current_key, method='DELETE')


if __name__ == '__main__':
    main()
