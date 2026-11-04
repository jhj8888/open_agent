"""Record UI cancellation or interrupt only the owned demo ingress during a run.

The browser submits/cancels tasks. This helper independently checks persisted
state and physical cleanup, and can inject a brief ingress crash to test SSE.
No model credentials or answer text are written into the evidence.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from uuid import UUID, uuid4

from demo_session import DemoSession
from provision_data import ROOT, command, private_file
from verify_model_run import workflow_result


def main():
    incoming = json.loads(sys.stdin.read())
    run_id = str(UUID(incoming['run_id']))
    mode = incoming.get('mode', 'collect')
    session = DemoSession()
    keys = json.loads((ROOT / 'api-secrets.json').read_text())
    headers = {'X-Platform-Key-Id': 'demo-supervisor', 'X-Platform-Service-Key': keys['reconciler_key']}

    def lookup(identifier):
        return session.request(session.api + '/internal/platform/reconcile/runs/' + identifier + '/lookup',
                               {'tenant': session.tenant, 'environment': 'test'}, headers)['execution']

    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'run_id': run_id, 'mode': mode}
    if mode == 'interrupt_ingress':
        for _ in range(25):
            row = lookup(run_id)
            if row and (row['sandboxPhase'] == 'created' or row['state'] in {'succeeded', 'failed', 'timed_out', 'cancelled'}):
                break
            time.sleep(1)
        if not row or row['state'] != 'running' or row['sandboxPhase'] != 'created':
            raise RuntimeError('Expected a running browser task with created sandbox')
        root = Path('/opt/open-agent/ingress')
        if root.is_symlink() or (root / '.owner').read_text().strip() != 'open_agent.ingress.demo.v1':
            raise RuntimeError('Ingress ownership check failed')
        unit = 'open-agent-ingress.service'
        configured = command('systemctl', 'show', unit, '--property=ExecStart', '--value').stdout
        if '/opt/open-agent/ingress/caddy-' not in configured:
            raise RuntimeError('Unexpected ingress executable')
        old_pid = command('systemctl', 'show', unit, '--property=MainPID', '--value').stdout.strip()
        deployment = json.loads((ROOT / 'demo-agent.json').read_text())
        accepted = session.call('/api/v1/agent-deployments/' + deployment['deployment_id'] + '/runs',
            {'input': {'message': '请简短说明统一 Agent 平台如何记录任务状态。'}, 'timeoutSeconds': 180},
            {'Idempotency-Key': 'console-concurrency-' + str(uuid4())})
        second_id = accepted['run_id']
        for _ in range(25):
            rows = [lookup(run_id), lookup(second_id)]
            if all(item and item['state'] == 'running' for item in rows):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError('No concurrent execution observed')
        leases = [item['lease']['leaseId'] for item in rows]
        if len(set(leases)) != 2 or rows[0]['unitName'] == rows[1]['unitName']:
            raise RuntimeError('Concurrent tasks did not have independent leases/processes')
        report.update({'concurrent_run_id': second_id, 'concurrent_running_observed': True,
                       'independent_leases': leases, 'old_ingress_pid': old_pid})
        # This intentionally drops active SSE connections. systemd restarts this
        # project-owned ingress; no other service or shared listener is touched.
        started = time.monotonic()
        command('systemctl', 'kill', '--kill-whom=main', '--signal=SIGKILL', unit)
        for _ in range(30):
            new_pid = command('systemctl', 'show', unit, '--property=MainPID', '--value').stdout.strip()
            if new_pid not in {'0', old_pid} and not command('systemctl', 'is-active', '--quiet', unit, check=False).returncode:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError('Owned ingress did not restart')
        after = lookup(run_id)
        if after['state'] != 'running' or after['lease']['leaseId'] != leases[0]:
            raise RuntimeError('Browser run did not survive ingress interruption')
        report.update({'new_ingress_pid': new_pid, 'restart_seconds': round(time.monotonic() - started, 3),
                       'task_survived_disconnect': True, 'shared_services_restarted': False})
    elif mode == 'collect':
        expected = incoming['expected_state']
        if expected not in {'succeeded', 'cancelled', 'failed', 'timed_out'}:
            raise RuntimeError('Unexpected terminal state')
        for _ in range(60):
            row = lookup(run_id)
            if row and row['phase'] == 'released':
                break
            time.sleep(1)
        if (not row or row['state'] != expected or row['phase'] != 'released'
                or row['sandboxPhase'] not in {None, 'released'} or row['leaseState'] != 'released'):
            raise RuntimeError('Run has not reached verified physical release')
        lease = row['lease']['leaseId']
        proof = json.loads((Path('/opt/open-agent/supervisor/state') / lease / 'cleanup.json').read_text())
        if command('docker', 'ps', '--all', '--quiet', '--filter', 'label=oa.lease=' + lease).stdout.strip():
            raise RuntimeError('Physical sandbox remained')
        events = []
        cursor = 0
        for _ in range(100):
            page = session.call('/api/v1/runs/' + run_id + '/events?after=' + str(cursor))['events']
            if not page:
                break
            if any(item['sequence_no'] <= cursor for item in page):
                raise RuntimeError('Event cursor regressed')
            events.extend(page)
            cursor = page[-1]['sequence_no']
        else:
            raise RuntimeError('Event collection exceeded limit')
        sequences = [item['sequence_no'] for item in events]
        if len(sequences) != len(set(sequences)) or sequences != sorted(sequences):
            raise RuntimeError('Duplicate or out-of-order events')
        answer = ''.join(item['payload']['text'] for item in events if item['event_type'] == 'message.delta')
        temporal = asyncio.run(workflow_result(run_id))
        if temporal['status'] != 'COMPLETED' or temporal['result']['state'] != expected:
            raise RuntimeError('Temporal terminal state differs')
        report.update({'state': expected, 'event_count': len(events), 'event_sequences_unique': True,
                       'answer_characters': len(answer), 'answer_sha256': hashlib.sha256(answer.encode()).hexdigest(),
                       'cleanup': proof, 'physical_container_absent': True, 'temporal': temporal,
                       'supervision_phase': row['phase'], 'lease_state': row['leaseState'], 'sandbox_phase': row['sandboxPhase']})
    else:
        raise RuntimeError('Unknown verification mode')
    private_file(ROOT / ('console-recovery-' + mode + '-' + run_id + '.json'), json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
