"""Real Temporal activities supervising isolated executor processes.

Only identifiers reach Temporal. Physical resource verification runs outside DB
transactions and terminal states retain quota until the independent proof commits.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
from typing import Awaitable, Callable
from uuid import uuid4
from weakref import WeakValueDictionary

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent.durable.child import ExecutorSettings
from agent.durable.contracts import ReconcileRequest, RunRef, RunResult, TERMINAL
from agent.durable.host_process import SystemdProcesses, WorkerProgram
from agent.durable.supervision import CleanupProof, ExecutionSnapshot, SupervisorClient
from agent.durable.workflow import EXECUTE_ACTIVITY, RECONCILE_ACTIVITY

LOG = logging.getLogger(__name__)


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode()


def directory(path: Path, mode: int):
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    info = path.stat()
    if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('supervisor_directory_unsafe')
    # systemd deliberately uses UMask=0077. Lease-only arguments must remain
    # traversable by the executor; credential/evidence directories stay 0700.
    path.chmod(mode)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def immutable(path: Path, data: bytes, mode: int):
    temporary = path.parent / ('.preparing-' + str(uuid4()))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if path.is_symlink() or path.stat().st_size != len(data) or path.read_bytes() != data:
                raise RuntimeError('supervisor_record_conflict') from None
    finally:
        temporary.unlink()
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


class AttemptFiles:
    """Root-owned lifecycle records. No prompt or model credential is written here."""
    STATE = Path('/opt/open-agent/supervisor/state')
    ARGUMENTS = Path('/opt/open-agent/agent-worker/attempts')

    def __init__(self, source: Path, python: Path, worker_template: dict):
        self.source, self.python, self.worker_template = source, python, worker_template
        directory(self.STATE, 0o700)
        directory(self.ARGUMENTS, 0o755)

    def prepare(self, snapshot: ExecutionSnapshot) -> WorkerProgram:
        lease = snapshot.lease
        template = json.loads(json.dumps(self.worker_template))
        if template.get('sandbox') is not None:
            template['sandbox']['admission_capability'] = secrets.token_urlsafe(40)
        settings = ExecutorSettings.model_validate({**template, 'lease_id': lease.leaseId})
        if settings.worker_id != lease.workerId:
            raise RuntimeError('supervisor_executor_identity_invalid')
        root = self.STATE / lease.leaseId
        directory(root, 0o700)
        # Explicit extraction is needed: Pydantic otherwise serializes SecretStr as a mask.
        value = settings.model_dump(mode='json')
        value['worker_key'] = settings.worker_key.get_secret_value()
        if settings.sandbox is not None:
            value['sandbox']['credential'] = settings.sandbox.credential.get_secret_value()
            value['sandbox']['admission_capability'] = settings.sandbox.admission_capability.get_secret_value()
        private = root / 'worker.json'
        argument = self.ARGUMENTS / (lease.leaseId + '.json')
        immutable(private, encoded(value), 0o600)
        immutable(argument, encoded(lease.model_dump(mode='json')), 0o644)
        return WorkerProgram(self.python, self.source, private, argument)

    def proof(self, snapshot: ExecutionSnapshot, process: dict, sandbox: dict) -> CleanupProof:
        root = self.STATE / snapshot.lease.leaseId
        directory(root, 0o700)
        path = root / 'cleanup.json'
        if path.exists():
            if path.is_symlink() or path.stat().st_size > 65536:
                raise RuntimeError('supervisor_record_conflict')
            saved = json.loads(path.read_bytes())
            result = CleanupProof.model_validate(saved['proof'])
            if (result.unitName != snapshot.unitName or result.invocationId != snapshot.invocationId
                    or result.sandboxId != snapshot.sandboxId
                    or hashlib.sha256(encoded(saved['evidence'])).hexdigest() != result.evidenceSha256):
                raise RuntimeError('supervisor_record_conflict')
            return result
        evidence = {'process': process, 'sandbox': sandbox}
        proof = CleanupProof(
            proofId=str(uuid4()), kind=process['kind'], bootId=process['bootId'], unitName=snapshot.unitName,
            invocationId=snapshot.invocationId, sandboxId=snapshot.sandboxId,
            evidenceSha256=hashlib.sha256(encoded(evidence)).hexdigest(),
            workerStopped=process['workerStopped'], cgroupEmpty=process['cgroupEmpty'],
            sandboxAbsent=sandbox['sandboxAbsent'], creationSettled=sandbox['creationSettled'],
        )
        immutable(path, encoded({'proof': proof.model_dump(mode='json'), 'evidence': evidence}), 0o600)
        return proof


@asynccontextmanager
async def heartbeats():
    async def pulse():
        while True:
            activity.heartbeat()
            await asyncio.sleep(5)
    heartbeat = asyncio.create_task(pulse())
    try:
        yield
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


class HostSupervisor:
    def __init__(self, api: SupervisorClient, processes: SystemdProcesses, files: AttemptFiles, *, maximum: int = 2,
                 sandbox_cleanup: Callable[[ExecutionSnapshot], Awaitable[tuple[ExecutionSnapshot, dict]]] | None = None):
        if type(maximum) is not int or not 1 <= maximum <= 16:
            raise ValueError('Invalid host execution capacity')
        self.api, self.processes, self.files = api, processes, files
        self.maximum, self.sandbox_cleanup = maximum, sandbox_cleanup
        self.claim_lock = asyncio.Lock()
        self.cleanup_locks = WeakValueDictionary()

    @activity.defn(name=EXECUTE_ACTIVITY)
    async def execute(self, run: RunRef) -> RunResult:
        try:
            async with heartbeats():
                return await self._execute(run)
        except asyncio.CancelledError:
            raise  # Workflow reconciliation and the independent scanner recover the bound attempt.
        except Exception as error:
            LOG.warning('Execution unconfirmed run_id=%s error_type=%s', run.run_id, type(error).__name__)
            raise ApplicationError('execution_unconfirmed', non_retryable=True) from None

    async def _execute(self, run: RunRef) -> RunResult:
        async with self.claim_lock:
            # Existing reservations count across supervisor restarts, including unknown launches.
            if len(await self.api.pending(limit=self.maximum)) >= self.maximum:
                current = await self.api.lookup(run)
                return RunResult(current.state if current.state in TERMINAL else 'queued')
            claim = await self.api.claim(run, self.processes.boot_id())
        if claim.lease is None:
            if claim.state in TERMINAL | {'queued'}:
                return RunResult(claim.state)
            raise RuntimeError('execution_already_admitted')
        snapshot = claim.execution
        program = self.files.prepare(snapshot)
        snapshot = await self.api.begin(snapshot.lease.leaseId)
        # Neither a lost begin reply nor a failed launch ever triggers a second launch call.
        unit = await self.processes.launch(snapshot.process(), program)
        await self.api.observed(snapshot.lease.leaseId, unit['InvocationID'])
        while True:
            snapshot = await self.api.inspect(snapshot.lease.leaseId)
            if snapshot.state in TERMINAL:
                return RunResult(snapshot.state)
            now = datetime.now(timezone.utc)
            if (snapshot.state == 'cancel_requested' or snapshot.deadlineAt <= now or snapshot.heartbeatUntil <= now):
                return RunResult((await self.api.fence(snapshot.lease.leaseId)).state)
            unit = await self.processes.inspect(snapshot.process())
            if (unit is None or unit['MainPID'] == '0' or unit['ActiveState'] != 'active'
                    or unit['InvocationID'] != snapshot.invocationId):
                return RunResult((await self.api.fence(snapshot.lease.leaseId)).state)
            await asyncio.sleep(2)

    @activity.defn(name=RECONCILE_ACTIVITY)
    async def reconcile(self, request: ReconcileRequest) -> RunResult:
        try:
            async with heartbeats():
                current = await self.api.lookup(request.run)
                if current.execution is None and current.state == 'queued':
                    current = await self.api.abandon(request.run)
                if current.execution is None:
                    if current.state not in TERMINAL:
                        raise RuntimeError('cleanup_binding_unavailable')
                    return RunResult(current.state)
                snapshot = await self.cleanup(current.execution)
                return RunResult(snapshot.state)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOG.warning('Cleanup unconfirmed run_id=%s error_type=%s', request.run.run_id, type(error).__name__)
            raise ApplicationError('cleanup_unconfirmed') from None

    async def cleanup(self, initial: ExecutionSnapshot) -> ExecutionSnapshot:
        lease_id = initial.lease.leaseId
        lock = self.cleanup_locks.setdefault(lease_id, asyncio.Lock())
        async with lock:
            snapshot = await self.api.inspect(lease_id)
            if snapshot.phase == 'released':
                if snapshot.leaseState != 'released' or snapshot.state not in TERMINAL:
                    raise RuntimeError('cleanup_state_inconsistent')
                return snapshot
            snapshot = await self.api.fence(lease_id)
            if snapshot.phase == 'launching' and snapshot.bootId == self.processes.boot_id():
                unit = await self.processes.inspect(snapshot.process())
                if unit is not None:
                    snapshot = await self.api.observed(lease_id, unit['InvocationID'])
            physical = await self.processes.stop(snapshot.process(), snapshot.invocationId, never_launched=snapshot.phase == 'prepared')
            # Read again after termination: the executor may have reported a resource while it stopped.
            snapshot = await self.api.inspect(lease_id)
            if self.sandbox_cleanup is not None:
                snapshot, sandbox = await self.sandbox_cleanup(snapshot)
            elif snapshot.sandboxPhase is None:
                sandbox = {'sandboxAbsent': True, 'creationSettled': True, 'reason': 'no_sandbox_creation_authorized'}
            else:
                raise RuntimeError('sandbox_cleanup_unavailable')
            proof = self.files.proof(snapshot, physical, sandbox)
            return await self.api.release(lease_id, proof)

    async def scan_once(self, *, startup: bool = False) -> int:
        cursor = None
        released = 0
        while True:
            rows = await self.api.pending(after=cursor, limit=100)
            for snapshot in rows:
                cursor = snapshot.lease.leaseId
                now = datetime.now(timezone.utc)
                if (startup or snapshot.state in TERMINAL or snapshot.state == 'cancel_requested'
                        or snapshot.deadlineAt <= now or snapshot.heartbeatUntil <= now):
                    try:
                        await self.cleanup(snapshot)
                        released += 1
                    except Exception as error:
                        LOG.warning('Reservation retained lease_id=%s error_type=%s', cursor, type(error).__name__)
            if len(rows) < 100:
                return released
