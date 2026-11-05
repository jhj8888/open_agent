"""Verify actual demo queue routing after the two existing model/Skill smoke checks.

Read-only: does not create tasks, consume outbox deliveries, or change admission.
Run with the frozen worker interpreter to use its official Temporal SDK.
"""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from temporalio.client import Client, WorkflowExecutionStatus

from demo_session import DemoSession
from provision_data import ROOT, private_file

sys.path.insert(0, '/opt/open-agent/recovery')
import recovery


async def main():
    session = DemoSession()
    supervisor = Path('/opt/open-agent/supervisor')
    ready = json.loads((supervisor / 'ready.json').read_text())
    queues = {'test': 'open-agent-demo-v1', 'prod': 'open-agent-demo-prod-v1'}
    assert ready['task_queues'] == queues
    pid = recovery.run('systemctl', 'show', 'open-agent-supervisor.service', '-p', 'MainPID', '--value').stdout.strip()
    assert str(ready['pid']) == pid and pid != '0'
    runtime = json.loads((Path('/opt/open-agent/agent-worker') / 'deployment.json').read_text())
    assert ready['source'] == runtime['source']
    assert recovery.sql('SELECT version FROM flyway_schema_history WHERE success=1 ORDER BY installed_rank DESC LIMIT 1;') == '1.64'
    recovery.assert_idle()
    production = json.loads((ROOT / 'promotion-verification.json').read_text())
    skill = json.loads((ROOT / 'skill-run-verification.json').read_text())
    runs = {'test': skill['run_id'], 'prod': production['production_model_run_id']}
    client = await Client.connect('127.0.0.1:17233', namespace='default')
    observations = {}
    intervals = []
    for environment, run_id in runs.items():
        status = session.call('/api/v1/runs/' + run_id)
        assert status['state'] == 'succeeded' and status['environment'] == environment
        description = await client.get_workflow_handle('open-agent/' + run_id).describe()
        assert description.task_queue == queues[environment]
        assert description.status == WorkflowExecutionStatus.COMPLETED
        assert description.close_time is not None
        intervals.append((description.start_time, description.close_time))
        observations[environment] = {
            'run_id': run_id, 'state': status['state'], 'task_queue': description.task_queue,
            'workflow_completed': True, 'started_at': description.start_time.isoformat(),
            'closed_at': description.close_time.isoformat(), 'usage': status['usage'],
        }
    for record in (json.loads((ROOT / 'demo-skill-agent.json').read_text()),
                   {'deployment_id': production['model_deployment_id']}):
        assert session.call('/api/v1/agent-deployments/' + record['deployment_id'])['admission_enabled']
    overlap = max(0.0, (min(end for _, end in intervals) - max(start for start, _ in intervals)).total_seconds())
    assert overlap > 0
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(), 'migration': '1.64',
        'worker_source_sha256': runtime['source_sha256'], 'task_queues': queues,
        'runs': observations, 'overlap_seconds': round(overlap, 3),
        'both_environments_admitted': True, 'no_active_tasks_or_unreleased_attempts': True,
        'scope': 'Two queues in one demo namespace and host; not Kubernetes/Kata physical isolation',
    }
    private_file(ROOT / 'environment-dispatch-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report))


if __name__ == '__main__':
    asyncio.run(main())
