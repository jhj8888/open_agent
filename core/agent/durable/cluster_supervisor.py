"""Temporal activities and restart-safe scanning for trusted Kubernetes executors.

The SQL ledger owns admission, launch authorization and quota. Process cleanup
uses positive receipts; local locks neither elect a leader nor prove termination.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
import logging
from uuid import UUID, uuid5
from weakref import WeakValueDictionary

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent.durable.cluster_control import ClusterCleanupProof, ClusterSnapshot, ClusterSupervisorClient
from agent.durable.cluster_lifecycle import ClusterLifecycle
from agent.durable.cluster_process import canonical
from agent.durable.contracts import ReconcileRequest, RunRef, RunResult, TERMINAL
from agent.durable.workflow import EXECUTE_ACTIVITY, RECONCILE_ACTIVITY

LOG = logging.getLogger(__name__)


@asynccontextmanager
async def heartbeats():
    async def pulse():
        while True:
            activity.heartbeat()
            await asyncio.sleep(5)
    task = asyncio.create_task(pulse())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def recovery_due(row: ClusterSnapshot, now: datetime) -> bool:
    return (row.state in TERMINAL or row.state == 'cancel_requested'
            or row.deadlineAt <= now or row.heartbeatUntil <= now)


class ClusterSupervisor:
    def __init__(self, api: ClusterSupervisorClient, lifecycle: ClusterLifecycle):
        if api is not lifecycle.control:
            raise ValueError('Cluster supervision binding mismatch')
        self.api, self.lifecycle = api, lifecycle
        self.cleanup_locks = WeakValueDictionary()

    def scoped(self, run: RunRef):
        if run.environment != self.api.target.environment:
            raise ApplicationError('activity_environment_mismatch', non_retryable=True)

    @activity.defn(name=EXECUTE_ACTIVITY)
    async def execute(self, run: RunRef) -> RunResult:
        self.scoped(run)
        try:
            async with heartbeats():
                return await self._execute(run)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOG.warning('Cluster execution unconfirmed run_id=%s error_type=%s', run.run_id, type(error).__name__)
            raise ApplicationError('execution_unconfirmed', non_retryable=True) from None

    async def _execute(self, run: RunRef) -> RunResult:
        self.scoped(run)
        # Cross-replica quota and one-shot binding are controlled by the DB.
        claim = await self.api.claim(run)
        if claim.lease is None:
            if claim.state in TERMINAL | {'queued'}:
                return RunResult(claim.state)
            raise RuntimeError('cluster_execution_already_admitted')
        row = await self.lifecycle.launch(claim.execution)
        while True:
            row = await self.api.inspect(row.lease.leaseId)
            if row.state in TERMINAL:
                return RunResult(row.state)
            if recovery_due(row, datetime.now(timezone.utc)):
                return RunResult((await self.api.fence(row.lease.leaseId)).state)
            pod = await self.lifecycle.processes.inspect(row)
            # Pending scheduling/image pulls do not prove failure or permit a
            # second creation. The existing finite lease/start deadline applies.
            if (pod is None or pod.metadata.deletion_timestamp is not None
                    or pod.status is not None and pod.status.phase in {'Succeeded', 'Failed', 'Unknown'}):
                return RunResult((await self.api.fence(row.lease.leaseId)).state)
            await asyncio.sleep(2)

    @activity.defn(name=RECONCILE_ACTIVITY)
    async def reconcile(self, request: ReconcileRequest) -> RunResult:
        self.scoped(request.run)
        try:
            async with heartbeats():
                lookup = await self.api.lookup(request.run)
                if lookup.execution is None and lookup.state == 'queued':
                    lookup = await self.api.abandon(request.run)
                if lookup.execution is None:
                    if lookup.state not in TERMINAL:
                        raise RuntimeError('cluster_cleanup_binding_unavailable')
                    return RunResult(lookup.state)
                row = await self.cleanup(lookup.execution)
                return RunResult(row.state)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOG.warning('Cluster cleanup pending run_id=%s error_type=%s', request.run.run_id, type(error).__name__)
            raise ApplicationError('cleanup_unconfirmed') from None

    @staticmethod
    def proof(row: ClusterSnapshot) -> ClusterCleanupProof:
        # This function may only run after lifecycle.stop_once confirmed absence.
        # No authorized sandbox creation => fencing also prevents a future create.
        # Kata admission/independent verification is a separate required adapter.
        if row.sandboxId is not None or row.sandboxPhase not in {None, 'reserved'}:
            raise RuntimeError('cluster_sandbox_cleanup_unavailable')
        receipt = row.termination or row.notStarted or row.creationRejection
        kinds = {'prepared': 'never_launched', 'terminated': 'terminated_pod', 'not_started': 'never_started_pod', 'rejected': 'create_rejected'}
        if row.state not in TERMINAL or row.phase not in kinds:
            raise RuntimeError('cluster_stop_unconfirmed')
        evidence = {'lease': row.lease.model_dump(mode='json'), 'target': row.target.model_dump(mode='json'),
                    'supervisorId': row.supervisorId, 'podName': row.podName, 'podUid': row.podUid,
                    'receipt': receipt.model_dump(mode='json') if receipt else None,
                    'sandboxPhase': row.sandboxPhase, 'sandboxId': row.sandboxId,
                    'sandboxReason': 'no_sandbox_creation_authorized'}
        digest = sha256(canonical(evidence)).hexdigest()
        return ClusterCleanupProof(proofId=str(uuid5(UUID(row.lease.leaseId), digest)), kind=kinds[row.phase],
            clusterId=row.target.clusterId, namespaceUid=row.target.namespaceUid, podName=row.podName,
            podUid=row.podUid, terminationId=receipt.id if receipt else None, sandboxId=None,
            evidenceSha256=digest, podAbsent=True, sandboxAbsent=True, creationSettled=True)

    async def cleanup(self, initial: ClusterSnapshot) -> ClusterSnapshot:
        lease_id = initial.lease.leaseId
        lock = self.cleanup_locks.setdefault(lease_id, asyncio.Lock())
        async with lock:
            row, absent = await self.lifecycle.stop_once(initial)
            if row.phase == 'released':
                return row
            if not absent:
                raise RuntimeError('cluster_process_cleanup_pending')
            # The executor may have reported a sandbox while it was stopping.
            row = await self.api.inspect(lease_id)
            if row.phase == 'released':
                return row
            return await self.api.release(lease_id, self.proof(row))

    async def scan_once(self) -> int:
        cursor, released = None, 0
        while True:
            rows = await self.api.pending(after=cursor, limit=100)
            for row in rows:
                cursor = row.lease.leaseId
                if recovery_due(row, datetime.now(timezone.utc)):
                    try:
                        await self.cleanup(row)
                        released += 1
                    except Exception as error:
                        LOG.warning('Cluster reservation retained lease_id=%s error_type=%s', cursor, type(error).__name__)
            if len(rows) < 100:
                return released
