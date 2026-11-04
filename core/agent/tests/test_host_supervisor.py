from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from agent.durable.control import ControlPlaneError
from agent.durable.host_process import HostProcessError
from agent.durable.supervision import ExecutionSnapshot, SupervisedClaim, RunLookup
from agent.durable.supervisor import HostSupervisor
from agent.tests.test_durable_supervision import snapshot


def fixture():
    raw = snapshot()
    now = datetime.now(timezone.utc)
    raw.update(heartbeatUntil=now + timedelta(seconds=30), deadlineAt=now + timedelta(minutes=2))
    prepared = ExecutionSnapshot.model_validate(raw)
    launching = prepared.model_copy(update={'phase': 'launching'})
    observed = launching.model_copy(update={'phase': 'observed', 'invocationId': 'a' * 32})
    finished = observed.model_copy(update={'state': 'succeeded', 'leaseState': 'cleanup_pending'})
    released = finished.model_copy(update={'phase': 'released', 'leaseState': 'released'})
    api = SimpleNamespace(pending=AsyncMock(return_value=[]),
                          claim=AsyncMock(return_value=SupervisedClaim(lease=prepared.lease, state='running', execution=prepared)),
                          lookup=AsyncMock(return_value=RunLookup(state='queued', execution=None)),
                          begin=AsyncMock(return_value=launching), observed=AsyncMock(return_value=observed),
                          inspect=AsyncMock(return_value=finished), fence=AsyncMock(return_value=finished),
                          release=AsyncMock(return_value=released))
    processes = SimpleNamespace(boot_id=Mock(return_value=prepared.bootId),
                                launch=AsyncMock(return_value={'InvocationID': observed.invocationId}),
                                inspect=AsyncMock(return_value=None), stop=AsyncMock(return_value={}))
    files = SimpleNamespace(prepare=Mock(return_value=object()), proof=Mock(return_value=object()))
    host = HostSupervisor(api, processes, files)
    return host, api, processes, files, prepared, observed, finished, released


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['begin', 'launch', 'observed'])
async def test_unknown_launch_steps_are_never_retried_or_treated_as_released(failure):
    host, api, processes, files, prepared, *_ = fixture()
    getattr(processes if failure == 'launch' else api, failure).side_effect = ControlPlaneError('CONTROL_UNAVAILABLE')
    with pytest.raises(ControlPlaneError):
        await host._execute(prepared.lease.attempt_lease().run)
    assert api.claim.await_count == 1
    assert api.begin.await_count == 1
    assert processes.launch.await_count == (0 if failure == 'begin' else 1)
    api.release.assert_not_awaited()
    files.proof.assert_not_called()


@pytest.mark.asyncio
async def test_host_capacity_counts_durable_reservations_before_claiming():
    host, api, processes, files, prepared, *_ = fixture()
    api.pending.return_value = [prepared, prepared]
    assert (await host._execute(prepared.lease.attempt_lease().run)).state == 'queued'
    api.claim.assert_not_awaited()
    files.prepare.assert_not_called()
    processes.launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_execution_observes_existing_database_success_without_releasing_resources():
    host, api, processes, files, prepared, *_ = fixture()
    assert (await host._execute(prepared.lease.attempt_lease().run)).state == 'succeeded'
    api.observed.assert_awaited_once_with(prepared.lease.leaseId, 'a' * 32)
    api.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_fences_stops_then_reads_late_sandbox_before_persisting_proof_and_releasing():
    host, api, processes, files, prepared, observed, finished, released = fixture()
    late = finished.model_copy(update={'sandboxPhase': 'created', 'sandboxId': str(uuid4())})
    api.inspect.side_effect = [finished, late]
    order = []
    async def fence(_):
        order.append('fence')
        return finished
    async def stop(*args, **kwargs):
        order.append('stop')
        return {'physical': True}
    async def sandbox(value):
        assert value.sandboxId == late.sandboxId
        order.append('sandbox')
        return late, {'sandboxAbsent': True, 'creationSettled': True}
    def proof(*args):
        order.append('persist-proof')
        return 'proof'
    async def release(*args):
        order.append('release')
        return released
    api.fence.side_effect, processes.stop.side_effect = fence, stop
    files.proof.side_effect, api.release.side_effect = proof, release
    host.sandbox_cleanup = sandbox
    assert (await host.cleanup(finished)).state == 'succeeded'
    assert order == ['fence', 'stop', 'sandbox', 'persist-proof', 'release']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['fence', 'stop', 'sandbox', 'proof'])
async def test_incomplete_cleanup_never_releases_quota(failure):
    host, api, processes, files, prepared, observed, finished, _ = fixture()
    if failure == 'fence':
        api.fence.side_effect = ControlPlaneError('CONTROL_UNAVAILABLE')
    elif failure == 'stop':
        processes.stop.side_effect = HostProcessError('HOST_LAUNCH_UNCONFIRMED')
    elif failure == 'sandbox':
        host.sandbox_cleanup = AsyncMock(side_effect=RuntimeError('unknown_create'))
    else:
        files.proof.side_effect = OSError('synthetic disk failure')
    with pytest.raises((RuntimeError, OSError)):
        await host.cleanup(finished)
    api.release.assert_not_awaited()
    if failure == 'fence':
        processes.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_sandbox_adapter_never_assumes_an_existing_reservation_was_removed():
    host, api, processes, files, prepared, observed, finished, _ = fixture()
    api.inspect.return_value = finished.model_copy(update={'sandboxPhase': 'creating'})
    with pytest.raises(RuntimeError, match='sandbox_cleanup_unavailable'):
        await host.cleanup(finished)
    files.proof.assert_not_called()
    api.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_compensation_scanner_continues_after_one_unknown_cleanup():
    host, api, processes, files, prepared, observed, finished, released = fixture()
    api.pending.return_value = [finished, finished]
    host.cleanup = AsyncMock(side_effect=[RuntimeError('unknown_create'), released])
    assert await host.scan_once() == 1
    assert host.cleanup.await_count == 2
