"""Real model/Skill exercise for admission-only suspension in an owned test tenant.

Creates three clearly labeled demo tasks: one finishes with a retained file, one
is cancelled after leaving the queue while suspended, and one is cancelled in
the queue. Secrets arrive on stdin and are never saved or printed. Existing
demo tenants are not suspended. Persistent markers prevent accidental replay.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from uuid import uuid4
import zipfile

import httpx
from demo_session import DemoSession
from provision_data import ROOT, private_file
from verify_task_outputs import raw

MARKER = ROOT / 'tenant-suspension-state.json'
REPORT = ROOT / 'tenant-suspension-verification.json'
NAME = '任务暂停验收 20261106'


def main():
    configuration = json.loads(sys.stdin.read(65537))
    session = DemoSession()
    session.api = 'https://120.26.81.83'
    state = json.loads(MARKER.read_text()) if MARKER.exists() else {}

    def save():
        private_file(MARKER, json.dumps(state, indent=2) + '\n', replace=True)

    if not state:
        assert not any(row['displayName'] == NAME for row in session.call('/platform/tenants')['tenants']), 'unowned_existing_tenant'
        state['tenant'] = session.call('/platform/tenants', {'displayName': NAME, 'ownerUid': session.uid})['tenantId']
        state['key'] = str(uuid4())
        save()
    tenant_path = '/platform/tenants/' + state['tenant']
    management = tenant_path + '/environments/test'

    def tenant():
        row = next(row for row in session.call('/platform/tenants')['tenants'] if row['tenantId'] == state['tenant'])
        assert row['displayName'] == NAME
        return row

    def status(value):
        row = tenant()
        if row['status'] != value:
            assert row['status'] in ('active', 'suspended')
            session.call(tenant_path + '/status', {'status': value, 'revision': row['revision']}, method='PUT')
        assert tenant()['status'] == value

    def run(identifier):
        return session.call('/api/v1/runs/' + identifier)

    def steps(identifier):
        rows, after = [], 0
        for _ in range(20):
            page = session.call('/api/v1/runs/' + identifier + '/observations?limit=100&after=' + str(after))
            rows.extend(page['items'])
            if not page['hasMore']:
                return rows
            after = page['next']
        raise RuntimeError('unexpected_step_volume')

    def executing(identifier):
        return any(row['kind'] == 'tool' and row['name'] == 'execute' and row['ended_at'] is None for row in steps(identifier))

    def cancel(identifier):
        return session.call('/api/v1/runs/' + identifier + '/cancel', {})

    def pause_interval(rows):
        paused = datetime.fromisoformat(state['paused_at'])
        def timestamp(value):
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        overlap = [row for row in rows if row['kind'] == 'tool' and row['name'] == 'execute'
                   and row['ended_at'] and timestamp(row['started_at']) <= paused < timestamp(row['ended_at'])]
        assert overlap, 'pause_did_not_overlap_execute_tool'
        return overlap[0]['step_id']

    if REPORT.exists():
        report = json.loads(REPORT.read_text())
        for value in report['runs'].values():
            assert run(value['id'])['state'] == value['state']
        report['paused_tool_step_id'] = pause_interval(steps(state['runs']['complete']))
        report['paused_at'] = state['paused_at']
        private_file(REPORT, json.dumps(report, indent=2) + '\n', replace=True)
        print(json.dumps({'previous_verified_report': report, 'tasks_replayed': False}), flush=True)
        return

    try:
        if 'runs' not in state:
            status('active')
            quota = session.call(management + '/quotas')
            session.call(management + '/quotas', {
                'maxConcurrentRuns': 1, 'maxQueuedRuns': 8, 'maxSandboxes': 1,
                'dailyTokenBudget': 1000000, 'auditRetentionDays': 180, 'revision': quota['revision']}, method='PUT')
            if 'model' not in state:
                state['model'] = session.call(management + '/models', {
                    'displayName': '暂停策略验收模型', 'endpoint': configuration['base_url'], 'modelName': configuration['model'],
                    'adapter': 'deepseek', 'thinkingEnabled': False, 'supportsStream': True, 'supportsTools': True, 'supportsUsage': True,
                    'connectTimeoutSeconds': 10, 'readTimeoutSeconds': 90, 'totalTimeoutSeconds': 180,
                    'apiKey': configuration['api_key'], 'expectedVersion': 0, 'revokePrevious': False})
                save()
            if 'skill' not in state:
                image = json.loads(Path('/opt/open-agent/sandbox/deployment.json').read_text())['runtime_image']
                memory = io.BytesIO()
                with zipfile.ZipFile(memory, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('SKILL.md', '---\nname: pause-drain-check\ndescription: 执行有界等待并生成验收文件\n---\n'
                                     '必须实际执行 `python3 /skills/pause-drain-check/scripts/check.py`，等待完成后报告检查码。禁止修改脚本或自行猜测结果。\n')
                    archive.writestr('scripts/check.py', 'import os,time\nfrom pathlib import Path\n'
                                     'assert os.getuid()==1000\ntime.sleep(12)\n'
                                     'root=Path("/workspace/outputs")\nroot.mkdir(parents=True,exist_ok=True)\n'
                                     'root.joinpath("pause-result.txt").write_text("PAUSE-DRAIN-6\\n",encoding="utf-8")\n'
                                     'print("PAUSE-DRAIN-6 uid="+str(os.getuid()))\n')
                with httpx.Client(trust_env=False, follow_redirects=False, timeout=45) as client:
                    response = client.post(session.api + management + '/skill-releases', headers=session.auth,
                                           data={'version': 'pause.1', 'runtimeImage': image},
                                           files={'archive': ('pause-drain-check.zip', memory.getvalue(), 'application/zip')})
                assert response.status_code == 200 and response.json()['code'] == 0, 'skill_publication_failed'
                state['skill'] = response.json()['data']['releaseId']
                save()
            if 'deployment' not in state:
                deployed = session.call(management + '/agent-deployments', {
                    'displayName': '暂停在途任务验收', 'agentVersion': 'pause.1',
                    'modelConnectionId': state['model']['connectionId'], 'modelVersion': state['model']['version'],
                    'skillReleaseIds': [state['skill']],
                    'execution': {'instructions': '按 pause-drain-check Skill 实际执行脚本，必须等待工具完成。最后用一句话报告真实检查码和文件。',
                                  'maxLoops': 8, 'temperature': 0.2, 'maxTokens': 1024}})
                state['deployment'] = deployed['deployment_id']
                save()
            route = '/api/v1/agent-deployments/' + state['deployment'] + '/runs'
            state['runs'] = {}
            # Persist the role/id immediately; all retries use the same key and body.
            save()
        route = '/api/v1/agent-deployments/' + state['deployment'] + '/runs'
        body = {'input': {'message': '请按 pause-drain-check Skill 实际执行脚本，等待其完成，报告检查码和产出文件。', 'history': []}, 'timeoutSeconds': 300}
        if not state.get('paused_at'):
            assert tenant()['status'] == 'active'
            if 'complete' not in state['runs']:
                state['runs']['complete'] = session.call(route, body, {'Idempotency-Key': state['key'] + '-complete'})['run_id']
                save()
            first = state['runs']['complete']
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline and not executing(first):
                assert run(first)['state'] in ('queued', 'running'), 'first_task_ended_before_pause'
                time.sleep(1)
            assert executing(first), 'execute_tool_not_observed'
            # Temporal scheduling order is not submission order. Establish a running
            # owner of the sole execution slot before enqueueing cancellation cases.
            for role in ('cancel_running', 'cancel_queued'):
                if role not in state['runs']:
                    state['runs'][role] = session.call(route, body, {'Idempotency-Key': state['key'] + '-' + role})['run_id']
                    save()
            second, third = state['runs']['cancel_running'], state['runs']['cancel_queued']
            assert run(second)['state'] == 'queued' and run(third)['state'] == 'queued'
            status('suspended')
            state['paused_at'] = datetime.now(timezone.utc).isoformat()
            save()
            print(json.dumps({'phase': 'suspended_with_running_tool_and_two_queued', 'tenant': state['tenant']}), flush=True)
        first, second, third = (state['runs'][role] for role in ('complete', 'cancel_running', 'cancel_queued'))
        assert tenant()['status'] == 'suspended', 'interrupted_exercise_requires_review_not_replay'
        try:
            session.call(route, body, {'Idempotency-Key': state['key'] + '-rejected-new'})
        except RuntimeError as error:
            assert str(error) == 'Demo API response status=403', 'unexpected_submission_denial'
        else:
            raise RuntimeError('new_task_was_admitted_while_suspended')
        assert cancel(third)['state'] == 'cancelled'
        deadline = time.monotonic() + 210
        while time.monotonic() < deadline:
            if not state.get('running_cancelled') and executing(second):
                assert tenant()['status'] == 'suspended'
                assert cancel(second)['state'] in ('cancel_requested', 'cancelled')
                state['running_cancelled'] = True
                save()
                print(json.dumps({'phase': 'accepted_queue_started_while_suspended_and_explicitly_cancelled'}), flush=True)
            one, two = run(first), run(second)
            if one['state'] in ('failed', 'cancelled', 'timed_out'):
                raise RuntimeError('accepted_task_did_not_finish_successfully')
            if one['state'] == 'succeeded' and two['state'] == 'cancelled' and state.get('running_cancelled'):
                break
            assert two['state'] not in ('succeeded', 'failed', 'timed_out'), 'second_task_ended_before_explicit_cancellation'
            time.sleep(1)
        assert one['state'] == 'succeeded' and two['state'] == 'cancelled' and state.get('running_cancelled')
        keys = json.loads((ROOT / 'api-secrets.json').read_text())
        headers = {'X-Platform-Key-Id': 'demo-supervisor', 'X-Platform-Service-Key': keys['reconciler_key']}
        executions = {}
        for identifier in (first, second):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                lookup = session.request('http://127.0.0.1:18082/internal/platform/reconcile/runs/' + identifier + '/lookup',
                                         {'tenant': state['tenant'], 'environment': 'test'}, headers)
                execution = lookup.get('execution')
                if execution and execution['phase'] == 'released':
                    break
                time.sleep(1)
            assert execution and execution['phase'] == execution['leaseState'] == execution['sandboxPhase'] == 'released'
            saved_proof = json.loads((Path('/opt/open-agent/supervisor/state') / execution['lease']['leaseId'] / 'cleanup.json').read_text())
            proof = saved_proof['proof']
            assert proof['kind'] == 'stopped_unit' and proof['sandboxId'] == execution['sandboxId']
            assert all(proof[field] is True for field in ('workerStopped', 'cgroupEmpty', 'sandboxAbsent', 'creationSettled'))
            executions[identifier] = {'phase': 'released', 'sandbox': 'released', 'proof_id': proof['proofId'],
                                      'evidence_sha256': proof['evidenceSha256']}
        rows = steps(first)
        paused_tool = pause_interval(rows)
        paused = datetime.fromisoformat(state['paused_at'])
        after_pause = [row for row in rows if row['kind'] == 'model' and datetime.fromisoformat(row['started_at'].replace('Z', '+00:00')) > paused]
        assert after_pause, 'no_model_step_after_suspension'
        files = session.call('/api/v1/runs/' + first + '/outputs')
        target = next(row for row in files if row['file_name'] == 'pause-result.txt')
        content, _ = raw(session, '/api/v1/runs/' + first + '/outputs/' + target['output_id'] + '/download')
        assert content == b'PAUSE-DRAIN-6\n' and hashlib.sha256(content).hexdigest() == target['sha256']
        quota = session.call(management + '/quotas')
        assert all(value == 0 for value in quota['usage'].values())
        assert len(session.call(management + '/runs')['items']) == state.get('prior_run_count', 0) + 3
        report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'tenant': state['tenant'], 'public_https': True,
                  'real_model_and_skill': True, 'paused_during_execute_tool': True, 'new_submission_denied': True,
                  'paused_at': state['paused_at'], 'paused_tool_step_id': paused_tool,
                  'accepted_queue_started_while_suspended': True, 'model_steps_after_pause': len(after_pause),
                  'retained_output_sha256': target['sha256'], 'output_download_verified': True,
                  'execution_and_sandbox_released': True, 'all_occupancy_zero': True,
                  'independent_cleanup_proofs': executions,
                  'earlier_fixture_runs_retained': state.get('prior_run_count', 0),
                  'runs': {role: {'id': identifier, 'state': run(identifier)['state']} for role, identifier in state['runs'].items()}}
        state['passed'] = True
        save()
    finally:
        # Restore only this owned tenant; never leave demo admission paused on a failed check.
        status('active')
    report['tenant_restored_active'] = tenant()['status'] == 'active'
    private_file(REPORT, json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
