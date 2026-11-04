"""Typed, bounded transport for the independent supervisor identity.

Only identifiers and lifecycle evidence pass this boundary. Model credentials,
prompts and artifacts remain on the separate executor-only runtime transport.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from agent.durable.contracts import RunRef, TERMINAL
from agent.durable.control import AttemptLease, ControlPlaneClient, ControlPlaneError
from agent.durable.host_process import ProcessBinding

Identifier = Annotated[str, Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")]
Identity = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
RunState = Literal["queued", "running", "cancel_requested", "succeeded", "failed", "timed_out", "cancelled"]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LeaseScope(WireModel):
    tenant: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
    environment: Literal["test", "prod"]


class SupervisedLease(WireModel):
    scope: LeaseScope
    runId: Identifier
    attempt: Annotated[int, Field(strict=True, ge=1, le=100)]
    leaseId: Identifier
    fencingToken: Annotated[int, Field(strict=True, ge=1)]
    workerId: Identity

    def attempt_lease(self) -> AttemptLease:
        return AttemptLease(RunRef(self.scope.tenant, self.scope.environment, self.runId),
                            self.attempt, self.leaseId, self.fencingToken)


class ExecutionSnapshot(WireModel):
    lease: SupervisedLease
    supervisorId: Identity
    bootId: Identifier
    unitName: Annotated[str, Field(pattern=r"^oa-exec-[a-f0-9]{32}\.service$")]
    invocationId: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")] | None
    phase: Literal["prepared", "launching", "observed", "released"]
    state: RunState
    leaseState: Literal["held", "cleanup_pending", "released"]
    heartbeatUntil: AwareDatetime
    deadlineAt: AwareDatetime
    sandboxPhase: Literal["reserved", "creating", "created", "cleanup_pending", "released"] | None
    sandboxId: Identifier | None
    isolationProfile: Literal["kata", "gvisor-demo"] | None
    runtimeImage: Annotated[str, Field(max_length=512)] | None

    def process(self) -> ProcessBinding:
        return ProcessBinding(self.lease.leaseId, self.bootId)


class SupervisedClaim(WireModel):
    lease: SupervisedLease | None
    state: RunState
    execution: ExecutionSnapshot | None


class CleanupProof(WireModel):
    proofId: Identifier
    kind: Literal["never_launched", "stopped_unit", "host_reboot"]
    bootId: Identifier
    unitName: Annotated[str, Field(pattern=r"^oa-exec-[a-f0-9]{32}\.service$")]
    invocationId: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")] | None
    sandboxId: Identifier | None
    evidenceSha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    workerStopped: Annotated[bool, Field(strict=True)]
    cgroupEmpty: Annotated[bool, Field(strict=True)]
    sandboxAbsent: Annotated[bool, Field(strict=True)]
    creationSettled: Annotated[bool, Field(strict=True)]


class SupervisorClient:
    def __init__(self, control: ControlPlaneClient, supervisor_id: str, worker_id: str):
        if control.http.headers.get("X-Platform-Key-Id") != supervisor_id:
            raise ValueError("Supervisor transport identity mismatch")
        self.control, self.supervisor_id, self.worker_id = control, supervisor_id, worker_id

    @staticmethod
    def _id(value: str) -> str:
        try:
            if str(UUID(value)) != value:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ControlPlaneError("CONTROL_SUPERVISION_INPUT_INVALID") from None
        return value

    def _snapshot(self, value: object, lease_id: str | None = None) -> ExecutionSnapshot:
        try:
            row = ExecutionSnapshot.model_validate(value)
            if (row.supervisorId != self.supervisor_id or row.lease.workerId != self.worker_id
                    or row.unitName != row.process().unit or (lease_id is not None and row.lease.leaseId != lease_id)
                    or (row.phase in {"prepared", "launching"} and row.invocationId is not None)
                    or (row.phase == "observed" and row.invocationId is None)):
                raise ValueError()
            return row
        except ValueError:
            raise ControlPlaneError("CONTROL_SUPERVISION_INVALID") from None

    async def claim(self, run: RunRef, boot_id: str) -> SupervisedClaim:
        data = await self.control.request(f"/internal/platform/reconcile/runs/{run.run_id}/claim",
            {"scope": {"tenant": run.tenant, "environment": run.environment}, "bootId": self._id(boot_id)})
        try:
            result = SupervisedClaim.model_validate(data)
            if result.lease is None:
                if result.execution is not None:
                    raise ValueError()
            else:
                execution = self._snapshot(result.execution)
                if (execution.lease != result.lease or result.lease.attempt_lease().run != run
                        or execution.bootId != boot_id or execution.phase != "prepared"
                        or execution.state != "running" or result.state != "running" or execution.leaseState != "held"):
                    raise ValueError()
            return result
        except ValueError:
            raise ControlPlaneError("CONTROL_SUPERVISION_INVALID") from None

    async def _post(self, lease_id: str, operation: str, body: dict | None = None) -> ExecutionSnapshot:
        lease_id = self._id(lease_id)
        data = await self.control.request(f"/internal/platform/reconcile/attempts/{lease_id}/{operation}", body or {})
        return self._snapshot(data, lease_id)

    async def inspect(self, lease_id: str) -> ExecutionSnapshot:
        return await self._post(lease_id, "inspect")

    async def begin(self, lease_id: str) -> ExecutionSnapshot:
        # No transport retry: a lost response is an unknown launch authorization.
        row = await self._post(lease_id, "begin")
        if row.phase != "launching" or row.state != "running" or row.leaseState != "held":
            raise ControlPlaneError("CONTROL_SUPERVISION_INVALID")
        return row

    async def observed(self, lease_id: str, invocation_id: str) -> ExecutionSnapshot:
        row = await self._post(lease_id, "observed", {"invocationId": invocation_id})
        if row.phase != "observed" or row.invocationId != invocation_id:
            raise ControlPlaneError("CONTROL_SUPERVISION_INVALID")
        return row

    async def fence(self, lease_id: str) -> ExecutionSnapshot:
        row = await self._post(lease_id, "fence")
        if row.state not in TERMINAL:
            raise ControlPlaneError("CONTROL_SUPERVISION_INVALID")
        return row

    async def adopt_sandbox(self, lease_id: str, sandbox_id: str) -> ExecutionSnapshot:
        row = await self._post(lease_id, "sandbox-observed", {"sandboxId": self._id(sandbox_id)})
        if row.sandboxId != sandbox_id or row.phase in {"prepared", "released"}:
            raise ControlPlaneError("CONTROL_SUPERVISION_INVALID")
        return row

    async def release(self, lease_id: str, proof: CleanupProof) -> ExecutionSnapshot:
        if not (proof.workerStopped and proof.cgroupEmpty and proof.sandboxAbsent and proof.creationSettled):
            raise ControlPlaneError("CONTROL_SUPERVISION_INPUT_INVALID")
        row = await self._post(lease_id, "release", proof.model_dump(mode="json"))
        if row.phase != "released" or row.leaseState != "released" or row.state not in TERMINAL:
            raise ControlPlaneError("CONTROL_SUPERVISION_INVALID")
        return row

    async def pending(self, after: str | None = None, limit: int = 100) -> list[ExecutionSnapshot]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ControlPlaneError("CONTROL_SUPERVISION_INPUT_INVALID")
        data = await self.control.request("/internal/platform/reconcile/pending",
                                          {"after": self._id(after) if after is not None else None, "limit": limit})
        if not isinstance(data, list) or len(data) > limit:
            raise ControlPlaneError("CONTROL_SUPERVISION_INVALID")
        rows = [self._snapshot(value) for value in data]
        cursor = after or ""
        for row in rows:
            if row.lease.leaseId <= cursor or row.phase == "released":
                raise ControlPlaneError("CONTROL_SUPERVISION_INVALID")
            cursor = row.lease.leaseId
        return rows
