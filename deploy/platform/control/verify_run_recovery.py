"""Fault-inject only newly submitted demo attempts, using real model/Skill services."""

import asyncio
from datetime import datetime, timezone
import json
import time
from uuid import uuid4

from demo_session import DemoSession
from provision_data import ROOT, command, private_file
from verify_model_run import workflow_result


def main():
    session = DemoSession()
    deployment = json.loads((ROOT / 'demo-skill-agent.json').read_text())
    keys = json.loads((ROOT / 'api-secrets.json').read_text())
    headers = {'X-Platform-Key-Id': 'demo-supervisor', 'X-Platform-Service-Key': keys['reconciler_key']}
    def lookup(run):
        return session.request(session.api + '/internal/platform/reconcile/runs/' + run + '/lookup',
                               {'tenant': session.tenant, 'environment': 'test'}, headers)['execution']
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'scenarios': [], 'shared_services_restarted': False}
    for operation in ['cancel', 'kill_executor']:
        started = time.monotonic()
        accepted = session.call('/api/v1/agent-deployments/' + deployment['deployment_id'] + '/runs',
            {'input': {'message': '请执行 isolation-check Skill，并说明输出。'}, 'timeoutSeconds': 180},
            {'Idempotency-Key': 'recovery-' + str(uuid4())})
        run = accepted['run_id']
        for _ in range(60):
            row = lookup(run)
            if row and row['sandboxPhase'] == 'created' and row['invocationId']:
                break
            if row and row['state'] in {'succeeded', 'failed', 'timed_out', 'cancelled'}:
                raise RuntimeError('Attempt finished before injection')
            time.sleep(1)
        else:
            raise RuntimeError('Sandbox creation was not acknowledged')
        lease = row['lease']['leaseId']
        if row['unitName'] != 'oa-exec-' + lease.replace('-', '') + '.service':
            raise RuntimeError('Unexpected executor identity')
        if operation == 'cancel':
            session.call(accepted['status_url'] + '/cancel', {})
            session.call(accepted['status_url'] + '/cancel', {})
            expected = 'cancelled'
        else:
            actual = command('systemctl', 'show', row['unitName'], '--property=InvocationID', '--value').stdout.strip()
            if actual != row['invocationId']:
                raise RuntimeError('Executor invocation changed')
            command('systemctl', 'kill', '--kill-whom=all', '--signal=SIGKILL', row['unitName'])
            expected = 'failed'
        injected = time.monotonic()
        for _ in range(70):
            row = lookup(run)
            if row['phase'] == 'released':
                break
            time.sleep(1)
        else:
            raise RuntimeError('Recovery did not release reservations')
        if row['state'] != expected or row['leaseState'] != 'released' or row['sandboxPhase'] != 'released':
            raise RuntimeError('Recovery state mismatch')
        containers = command('docker', 'ps', '--all', '--quiet', '--filter', 'label=oa.lease=' + lease).stdout.strip()
        if containers:
            raise RuntimeError('Sandbox remained after release')
        result = asyncio.run(workflow_result(run))
        if result['status'] != 'COMPLETED' or result['result']['state'] != expected:
            raise RuntimeError('Workflow recovery result mismatch')
        record = {'operation': operation, 'run_id': run, 'state': expected,
                  'recovery_seconds': round(time.monotonic() - injected, 3),
                  'total_seconds': round(time.monotonic() - started, 3),
                  'worker_released': True, 'sandbox_released': True, 'physical_container_absent': True,
                  'temporal': result}
        report['scenarios'].append(record)
        print(json.dumps(record), flush=True)
        private_file(ROOT / 'run-recovery-verification.json', json.dumps(report, indent=2) + '\n', replace=True)


if __name__ == '__main__':
    main()
