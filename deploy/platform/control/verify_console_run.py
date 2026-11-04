"""Read-only evidence for a task submitted through the real browser workbench."""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID

from demo_session import DemoSession
from provision_data import ROOT, private_file
from verify_model_run import workflow_result


def main():
    run_id = str(UUID(json.loads(sys.stdin.read())['run_id']))
    session = DemoSession()
    status = session.call('/api/v1/runs/' + run_id)
    keys = json.loads((ROOT / 'api-secrets.json').read_text())
    lookup = session.request(session.api + '/internal/platform/reconcile/runs/' + run_id + '/lookup',
                             {'tenant': session.tenant, 'environment': 'test'},
                             {'X-Platform-Key-Id': 'demo-supervisor', 'X-Platform-Service-Key': keys['reconciler_key']})
    execution = lookup.get('execution')
    if not execution or execution['phase'] != 'released' or execution['sandboxPhase'] != 'released':
        raise RuntimeError('Browser task resources not verified released')
    events = []
    cursor = 0
    for _ in range(100):
        page = session.call('/api/v1/runs/' + run_id + '/events?after=' + str(cursor))
        events.extend(page['events'])
        if len(page['events']) < 100:
            break
        cursor = page['events'][-1]['sequence_no']
    else:
        raise RuntimeError('Browser task event limit exceeded')
    text = ''.join(event['payload']['text'] for event in events if event['event_type'] == 'message.delta')
    proof = json.loads((Path('/opt/open-agent/supervisor/state') / execution['lease']['leaseId'] / 'cleanup.json').read_text())
    tools = [event['payload']['name'] for event in events if event['event_type'] == 'tool.finished']
    temporal = asyncio.run(workflow_result(run_id))
    if (status['state'] != 'succeeded' or execution['leaseState'] != 'released' or temporal['status'] != 'COMPLETED'
            or 'execute' not in tools or not all(value in text for value in ['1000', '5420', 'SKILL-READY-5420'])):
        raise RuntimeError('Browser Skill task verification failed')
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'run_id': run_id,
              'state': status['state'], 'usage': status.get('usage'), 'event_count': len(events),
              'answer_sha256': hashlib.sha256(text.encode()).hexdigest(), 'answer_characters': len(text),
              'tools': tools, 'cleanup': proof, 'temporal': temporal,
              'supervision_phase': execution['phase'], 'sandbox_phase': execution['sandboxPhase'],
              'lease_state': execution['leaseState'], 'script_output_verified': True}
    private_file(ROOT / 'console-run-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
