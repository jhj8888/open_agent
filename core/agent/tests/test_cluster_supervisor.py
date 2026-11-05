"""Supervisor ordering against a synthetic ledger/API, plus real Temporal orchestration.

These tests never represent synthetic Pod status as actual node termination.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from agent.durable.cluster_control import ClusterClaim, ClusterLookup
from agent.durable.cluster_lifecycle import ClusterLifecycle
from agent.durable.cluster_supervisor import ClusterSupervisor
from agent.durable.contracts import ReconcileRequest, RunResult, TERMINAL
from agent.durable.workflow import AgentRunWorkflow
from agent.tests.test_cluster_process import system, reject_create
from agent.tests.test_durable_workflow import binary


class Ledger:
    """Only an orchestration fixture; real ledger concurrency has separate MySQL tests."""
    def __init__(self, row):
        now = datetime.now(timezone.utc)
        self.row = row.model_copy(update={'phase': 'prepared', 'heartbeatUntil': now+timedelta(seconds=120),
                                         'deadlineAt': now+timedelta(minutes=5)})
        self.target = row.target
        self.events, self.proofs = [], []
        self.launched = asyncio.Event()
        self.lose = None
        self.busy = False

    async def claim(self, run):
        self.events.append('claim')
        if self.busy:
            return ClusterClaim(lease=None, execution=None, state='queued')
        return ClusterClaim(lease=self.row.lease, execution=self.row, state='running')

    async def inspect(self, lease):
        self.events.append('inspect')
        return self.row

    async def begin(self, lease):
        self.events.append('begin')
        assert self.row.phase == 'prepared'
        self.row = self.row.model_copy(update={'phase': 'launching'})
        if self.lose == 'begin':
            raise OSError('synthetic lost begin response')
        return self.row

    async def observed(self, lease, uid, version):
        self.events.append('observed')
        self.row = self.row.model_copy(update={'phase':'observed', 'podUid':uid, 'observedResourceVersion':version})
        self.launched.set()
        if self.lose == 'observed':
            raise OSError('synthetic lost UID response')
        return self.row

    async def fence(self, lease):
        self.events.append('fence')
        state = self.row.state
        if state not in TERMINAL:
            state = 'cancelled' if state == 'cancel_requested' else 'failed'
        self.row = self.row.model_copy(update={'state':state,'leaseState':'cleanup_pending'})
        return self.row

    async def not_started(self, lease, receipt):
        self.events.append('not_started')
        self.row = self.row.model_copy(update={'phase':'not_started','notStarted':receipt})
        return self.row

    async def creation_rejected(self, lease, receipt):
        self.events.append('creation_rejected')
        if self.lose == 'rejection_before_commit':
            raise OSError('synthetic lost write')
        state = 'cancelled' if self.row.state == 'cancel_requested' else 'failed'
        self.row = self.row.model_copy(update={'phase':'rejected','state':state,'leaseState':'cleanup_pending','creationRejection':receipt})
        if self.lose == 'rejection_after_commit':
            raise OSError('synthetic lost receipt reply')
        return self.row

    async def terminated(self, lease, receipt):
        self.events.append('terminated')
        self.row = self.row.model_copy(update={'phase':'terminated','termination':receipt})
        return self.row

    async def release(self, lease, proof):
        self.events.append('release')
        self.proofs.append(proof)
        self.row = self.row.model_copy(update={'phase':'released','leaseState':'released'})
        return self.row

    async def pending(self, after=None, limit=100):
        return [] if self.row.phase == 'released' or after is not None else [self.row]

    async def lookup(self, run):
        return ClusterLookup(state=self.row.state,execution=self.row)


def harness(system):
    row, driver, server = system
    ledger = Ledger(row)
    supervisor = ClusterSupervisor(ledger, ClusterLifecycle(ledger,driver))
    return ledger, supervisor, server


@pytest.mark.asyncio
@pytest.mark.parametrize('loss',[None,'rejection_before_commit','rejection_after_commit'])
async def test_create_rejection_recovery_does_not_repeat_create_or_invent_a_receipt(system,loss):
    ledger, supervisor, server = harness(system)
    reject_create(server,ledger.row)
    ledger.lose = loss
    run = ledger.row.lease.attempt_lease().run
    if loss:
        with pytest.raises(ApplicationError,match='execution_unconfirmed'):
            await ActivityEnvironment().run(supervisor.execute,run)
    else:
        assert await supervisor._execute(run) == RunResult('failed')
    if loss == 'rejection_before_commit':
        with pytest.raises(Exception,match='CLUSTER_LAUNCH_UNCONFIRMED'):
            await supervisor.cleanup(ledger.row)
        assert not ledger.proofs and ledger.row.phase == 'launching'
    else:
        results = await asyncio.gather(supervisor.cleanup(ledger.row),supervisor.cleanup(ledger.row))
        assert all(r.phase == 'released' for r in results)
        assert len(ledger.proofs) == 1 and ledger.proofs[0].kind == 'create_rejected'
        assert ledger.proofs[0].terminationId == ledger.row.creationRejection.id
    assert ledger.events.count('begin') == 1 and sum(c[0]=='POST' for c in server.calls) == 1
    assert not any(c[0] in {'PATCH','DELETE'} for c in server.calls)


@pytest.mark.asyncio
async def test_foreign_environment_is_rejected_before_any_control_call(system):
    ledger, supervisor, _ = harness(system)
    run = ledger.row.lease.attempt_lease().run
    wrong = type(run)(run.tenant, 'prod', run.run_id)
    with pytest.raises(ApplicationError, match='activity_environment_mismatch'):
        await supervisor.execute(wrong)
    with pytest.raises(ApplicationError, match='activity_environment_mismatch'):
        await supervisor.reconcile(ReconcileRequest(wrong,'outcome_unknown'))
    assert ledger.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize('loss', ['begin','create','observed'])
async def test_unknown_launch_is_not_repeated_and_never_claimed_released(system, loss):
    ledger, supervisor, server = harness(system)
    ledger.lose = loss
    server.fail_create = loss == 'create'
    run = ledger.row.lease.attempt_lease().run
    with pytest.raises(ApplicationError, match='execution_unconfirmed'):
        await ActivityEnvironment().run(supervisor.execute, run)
    assert ledger.events.count('begin') == 1 and not ledger.proofs
    assert len([c for c in server.calls if c[0]=='POST']) == int(loss != 'begin')
    assert not any(c[0]=='PATCH' for c in server.calls)
    if loss == 'begin':
        with pytest.raises(Exception, match='CLUSTER_LAUNCH_UNCONFIRMED'):
            await supervisor.cleanup(ledger.row)
        assert not ledger.proofs


@pytest.mark.asyncio
async def test_busy_claim_performs_no_process_operation(system):
    ledger, supervisor, server = harness(system)
    ledger.busy = True
    assert await supervisor._execute(ledger.row.lease.attempt_lease().run) == RunResult('queued')
    assert ledger.events == ['claim'] and not server.calls


@pytest.mark.asyncio
async def test_new_replica_scan_preserves_healthy_existing_lease(system):
    ledger, supervisor, server = harness(system)
    await supervisor.lifecycle.launch(ledger.row)
    before = list(server.calls)
    assert await supervisor.scan_once() == 0
    assert server.calls == before and 'fence' not in ledger.events
    assert ledger.events.count('begin') == 1


@pytest.mark.asyncio
async def test_never_launched_cleanup_and_duplicate_reconcile_release_once(system):
    ledger, supervisor, server = harness(system)
    results = await asyncio.gather(supervisor.cleanup(ledger.row),supervisor.cleanup(ledger.row))
    assert all(r.phase == 'released' and r.state == 'failed' for r in results)
    assert len(ledger.proofs) == 1 and ledger.proofs[0].kind == 'never_launched'
    assert not any(c[0] in {'POST','PATCH','DELETE'} for c in server.calls)


@pytest.mark.asyncio
async def test_pending_pod_cancellation_requires_two_passes_and_saved_receipt(system):
    ledger, supervisor, server = harness(system)
    await supervisor.lifecycle.launch(ledger.row)
    ledger.row = ledger.row.model_copy(update={'state':'cancel_requested'})
    with pytest.raises(RuntimeError, match='cluster_process_cleanup_pending'):
        await supervisor.cleanup(ledger.row)
    assert server.pod['metadata']['deletionTimestamp'] and not ledger.proofs
    result = await supervisor.cleanup(ledger.row)
    assert result.state == 'cancelled' and result.phase == 'released' and server.pod is None
    assert ledger.events.index('not_started') < ledger.events.index('release')
    assert ledger.proofs[0].kind == 'never_started_pod'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['creating','created','cleanup_pending','released'])
async def test_authorized_sandbox_without_independent_verifier_keeps_quota(system, phase):
    ledger, supervisor, server = harness(system)
    await supervisor.lifecycle.launch(ledger.row)
    server.terminated()
    ledger.row = ledger.row.model_copy(update={'state':'succeeded','sandboxPhase':phase,
        'sandboxId':None if phase == 'creating' else str(uuid4())})
    with pytest.raises(RuntimeError, match='cluster_process_cleanup_pending'):
        await supervisor.cleanup(ledger.row)
    assert not ledger.proofs
    with pytest.raises(RuntimeError, match='cluster_sandbox_cleanup_unavailable'):
        await supervisor.cleanup(ledger.row)
    assert ledger.row.phase == 'terminated' and server.pod is None and not ledger.proofs


@pytest.mark.asyncio
async def test_late_sandbox_report_is_read_after_positive_process_stop(system):
    ledger, supervisor, _ = harness(system)
    stopped = ledger.row.model_copy(update={'state':'failed','leaseState':'cleanup_pending'})
    supervisor.lifecycle.stop_once = AsyncMock(return_value=(stopped,True))
    ledger.row = stopped.model_copy(update={'sandboxPhase':'creating'})
    with pytest.raises(RuntimeError, match='cluster_sandbox_cleanup_unavailable'):
        await supervisor.cleanup(stopped)
    assert ledger.events == ['inspect'] and not ledger.proofs


@pytest.mark.asyncio
async def test_success_state_remains_success_after_receipt_and_release(system):
    ledger, supervisor, server = harness(system)
    await supervisor.lifecycle.launch(ledger.row)
    server.terminated()
    ledger.row = ledger.row.model_copy(update={'state':'succeeded'})
    with pytest.raises(RuntimeError, match='cluster_process_cleanup_pending'):
        await supervisor.cleanup(ledger.row)
    result = await supervisor.cleanup(ledger.row)
    assert result.state == 'succeeded' and result.phase == 'released'
    assert ledger.proofs[0].kind == 'terminated_pod' and server.pod is None


def test_recovery_proof_is_stable_after_restart(system):
    ledger, _, _ = harness(system)
    row = ledger.row.model_copy(update={'state':'failed','sandboxPhase':'reserved'})
    assert ClusterSupervisor.proof(row) == ClusterSupervisor.proof(row.model_copy(deep=True))


@pytest.mark.asyncio
async def test_real_temporal_runs_cluster_activity_then_reconciles_without_relaunch(system):
    ledger, supervisor, server = harness(system)
    run = ledger.row.lease.attempt_lease().run
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=binary()) as env:
        queue = 'open-agent-cluster-test-' + uuid4().hex
        async with Worker(env.client,task_queue=queue,workflows=[AgentRunWorkflow],
                          activities=[supervisor.execute,supervisor.reconcile]):
            handle = await env.client.start_workflow(AgentRunWorkflow.run,run,id='cluster-'+uuid4().hex,task_queue=queue)
            await asyncio.wait_for(ledger.launched.wait(),10)
            # Let the actual SDK activation finish before synthetic completion.
            for _ in range(100):
                if not server.pod['spec'].get('schedulingGates'):
                    break
                await asyncio.sleep(.01)
            assert not server.pod['spec'].get('schedulingGates')
            server.terminated()
            ledger.row = ledger.row.model_copy(update={'state':'succeeded'})
            assert await asyncio.wait_for(handle.result(),15) == RunResult('succeeded')
    assert ledger.row.phase == 'released' and ledger.events.count('begin') == 1
    assert len([c for c in server.calls if c[0]=='POST']) == 1
