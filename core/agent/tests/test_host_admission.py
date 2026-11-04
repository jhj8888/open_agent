from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

import httpx
import pytest

from agent.durable.child import ExecutorSettings, execute_attempt
from agent.durable.control import AttemptLease
from agent.durable.contracts import RunRef
from agent.durable.host_admission import HostAdmissionClient
from agent.durable.supervision import SupervisedLease
from agent.platform_runtime.sandbox import RunScope, SandboxFailure


def fixture():
    lease = AttemptLease(RunRef('tenant', 'test', str(uuid4())), 1, str(uuid4()), 1)
    scope = RunScope('tenant', 'test', UUID(lease.run.run_id), 1, UUID(lease.lease_id),
                     datetime.now(timezone.utc) + timedelta(minutes=5))
    return lease, scope


@pytest.mark.asyncio
async def test_host_admission_sends_bound_capability_and_never_accepts_foreign_lease():
    lease, scope = fixture()
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'verified': True})
    client = HostAdmissionClient(lease, '/run/open-agent/supervisor.sock', 'c' * 40, transport=httpx.MockTransport(respond))
    try:
        await client.before_create(scope, 'gvisor-demo', 'image@sha256:' + 'a' * 64)
        sandbox_id = str(uuid4())
        await client.after_create(scope, 'gvisor-demo', sandbox_id)
        assert calls[0].headers['X-Execution-Capability'] == 'c' * 40
        assert calls[0].url.path == f'/v1/attempts/{lease.lease_id}/before'
        assert json.loads(calls[1].content) == {'profile': 'gvisor-demo', 'sandboxId': sandbox_id}
        for other in (replace(scope, tenant_id='other'), replace(scope, environment='prod'),
                      replace(scope, run_id=uuid4()), replace(scope, lease_id=uuid4()), replace(scope, attempt=2)):
            with pytest.raises(SandboxFailure, match='HOST_ADMISSION_SCOPE_MISMATCH'):
                await client.before_create(other, 'gvisor-demo', 'ignored')
        assert len(calls) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,body', [(200, {'verified': False}), (200, {'verified': 1}),
                                      (200, {'verified': True, 'untrusted': 'payload'}), (200, None),
                                      (302, {'verified': True}), (403, {'verified': True})])
async def test_host_admission_fails_closed_on_invalid_evidence_or_redirect(status, body):
    lease, scope = fixture()
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, json=body, headers={'Location': 'http://untrusted.invalid'})
    client = HostAdmissionClient(lease, '/run/open-agent/supervisor.sock', 'c' * 40, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(SandboxFailure, match='HOST_ISOLATION_UNVERIFIED'):
            await client.before_create(scope, 'gvisor-demo', 'ignored')
        assert len(calls) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_executor_rejects_wrong_attempt_credentials_before_any_runtime_io():
    lease, _ = fixture()
    record = SupervisedLease.model_validate({'scope': {'tenant': 'tenant', 'environment': 'test'},
        'runId': lease.run.run_id, 'attempt': 1, 'leaseId': lease.lease_id, 'fencingToken': 1, 'workerId': 'worker'})
    settings = ExecutorSettings(lease_id=str(uuid4()), worker_id='worker', worker_key='private-worker-key' * 3,
                                control_endpoint='http://127.0.0.1:18082', model_origins=[])
    assert 'private-worker-key' not in repr(settings)
    with pytest.raises(RuntimeError, match='^executor_configuration_invalid$'):
        await execute_attempt(settings, record)


def test_executor_diagnostics_exclude_exception_message_and_locals():
    from agent.durable.child import failure_location
    credential = 'private-test-credential-do-not-log'
    try:
        raise ValueError(credential)
    except ValueError as error:
        diagnostic = failure_location(error)
    assert credential not in json.dumps(diagnostic)
    assert diagnostic['error_type'] == 'ValueError'
    assert diagnostic['frames'][-1]['function'] == 'test_executor_diagnostics_exclude_exception_message_and_locals'


def test_control_diagnostics_use_only_fixed_categories():
    from agent.durable.child import failure_location
    from agent.durable.control import ControlPlaneError
    diagnostic = failure_location(ControlPlaneError('untrusted-private-key', 'read_timeout'))
    assert diagnostic['control_category'] == 'read_timeout'
    assert 'untrusted-private-key' not in json.dumps(diagnostic)
    assert failure_location(ControlPlaneError('CONTROL_UNAVAILABLE', 'untrusted-private-key'))['control_category'] == 'unspecified'
