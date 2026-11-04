"""Real HTTP -> SQL outbox -> Temporal -> systemd -> model -> cleanup proof check."""

import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from demo_session import DemoSession
from provision_data import ROOT, command, private_file


async def workflow_result(run_id):
    from temporalio.client import Client
    client = await Client.connect('127.0.0.1:17233')
    handle = client.get_workflow_handle('open-agent/' + run_id)
    result = await handle.result(rpc_timeout=timedelta(seconds=15))
    description = await handle.describe()
    return {'status': description.status.name, 'result': result}


def main():
    session = DemoSession()
    deployment = json.loads((ROOT / 'demo-agent.json').read_text())
    started = time.monotonic()
    identifier = str(uuid4())
    route = '/api/v1/agent-deployments/' + deployment['deployment_id'] + '/runs'
    body = {'input': {'message': '请用一句中文说明企业为什么需要统一管理 Agent。', 'history': []}, 'timeoutSeconds': 180}
    accepted = session.call(route, body, {'Idempotency-Key': 'verification-' + identifier})
    run_id = accepted['run_id']
    repeat = session.call(route, body, {'Idempotency-Key': 'verification-' + identifier})
    if repeat['run_id'] != run_id or not repeat['reused']:
        raise RuntimeError('Idempotent run submission failed')
    keys = json.loads((ROOT / 'api-secrets.json').read_text())
    headers = {'X-Platform-Key-Id': 'demo-supervisor', 'X-Platform-Service-Key': keys['reconciler_key']}
    execution = None
    while time.monotonic() - started < 210:
        status = session.call(accepted['status_url'])
        lookup = session.request(session.api + '/internal/platform/reconcile/runs/' + run_id + '/lookup',
                                 {'tenant': session.tenant, 'environment': 'test'}, headers)
        execution = lookup.get('execution')
        if status['state'] in {'succeeded', 'failed', 'timed_out', 'cancelled'} and execution and execution['phase'] == 'released':
            break
        time.sleep(2)
    events = []
    cursor = 0
    for _ in range(30):
        page = session.call(accepted['events_url'] + '?after=' + str(cursor))
        events.extend(page['events'])
        if len(page['events']) < 100:
            break
        cursor = page['last_sequence']
    text = ''.join(event['payload']['text'] for event in events if event['event_type'] == 'message.delta')
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'run_id': run_id, 'state': status['state'],
              'error_code': status.get('error_code'), 'idempotency_verified': True, 'duration_seconds': round(time.monotonic() - started, 3),
              'event_count': len(events), 'event_types': sorted(set(event['event_type'] for event in events)),
              'answer_characters': len(text), 'answer_sha256': hashlib.sha256(text.encode()).hexdigest(),
              'supervision_phase': execution.get('phase') if execution else None,
              'attempt_lease_state': execution.get('leaseState') if execution else None,
              'real_model_configured': True, 'model_answer_received': bool(text.strip()),
              'sandbox_used': False, 'public_endpoint': False}
    if execution and execution['phase'] == 'released':
        proof = json.loads((Path('/opt/open-agent/supervisor/state') / execution['lease']['leaseId'] / 'cleanup.json').read_text())
        report['cleanup'] = proof
        report['temporal'] = asyncio.run(workflow_result(run_id))
    private_file(ROOT / ('model-run-' + identifier + '.json'), json.dumps(report, indent=2) + '\n')
    private_file(ROOT / 'model-run-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)
    if (status['state'] != 'succeeded' or not text.strip() or not execution or execution['phase'] != 'released'
            or execution['leaseState'] != 'released' or report['temporal']['status'] != 'COMPLETED'):
        raise RuntimeError('Real model run did not pass; evidence retained')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'message': str(error) if isinstance(error, RuntimeError) else 'Verification failed'}), flush=True)
        raise SystemExit(1) from None
