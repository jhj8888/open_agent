"""Strict cluster-supervisor transport; no model/Skill data or Kubernetes credentials.

This is the ledger side of cluster execution. It does not make API deletion or
404 a termination proof and it never retries a one-shot launch authorization.
"""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from agent.durable.contracts import RunRef, TERMINAL
from agent.durable.control import ControlPlaneClient, ControlPlaneError
from agent.durable.supervision import Identifier, Identity, RunState, SupervisedLease, SupervisorClient, WireModel

Version = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
PodName = Annotated[str, Field(pattern=r"^oa-exec-[a-f0-9]{32}$")]


class ClusterTarget(WireModel):
    workerId: Identity
    environment: Literal["test", "prod"]
    clusterId: Identifier
    namespace: Annotated[str, Field(max_length=63, pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")]
    namespaceUid: Identifier
    executorImage: Annotated[str, Field(max_length=512, pattern=r"^[a-z0-9][a-z0-9.:/_-]*@sha256:[a-f0-9]{64}$")]


class PodTermination(WireModel):
    id: Identifier
    podUid: Identifier
    resourceVersion: Version
    nodeName: Annotated[str, Field(max_length=253, pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*$")]
    nodeUid: Identifier
    podPhase: Literal["Succeeded", "Failed"]
    containerName: Literal["executor"]
    containerId: Annotated[str, Field(pattern=r"^[a-z][a-z0-9+.-]{0,20}://[A-Za-z0-9_.:-]{1,256}$")]
    exitCode: Annotated[int, Field(strict=True, ge=0, le=255)]
    startedAt: AwareDatetime
    finishedAt: AwareDatetime
    evidenceSha256: Hash

    def consistent(self):
        return self.finishedAt >= self.startedAt and (self.podPhase != "Succeeded" or self.exitCode == 0)


class PodNotStarted(WireModel):
    id: Identifier
    podUid: Identifier
    resourceVersion: Version
    deletionTimestamp: AwareDatetime
    schedulingGate: Literal["open-agent.io/execution-authorized"] | None
    nodeUnbound: Annotated[bool, Field(strict=True)]
    containerStatusesEmpty: Annotated[bool, Field(strict=True)]
    evidenceSha256: Hash


class PodCreationRejection(WireModel):
    id: Identifier
    auditId: Identifier
    podName: PodName
    namespaceUid: Identifier
    httpStatus: Annotated[int, Field(strict=True)]
    reason: Literal["Unauthorized", "Forbidden", "Invalid"]
    requestSha256: Hash
    responseSha256: Hash

    def consistent(self):
        return {401: "Unauthorized", 403: "Forbidden", 422: "Invalid"}.get(self.httpStatus) == self.reason


class ClusterSnapshot(WireModel):
    lease: SupervisedLease
    supervisorId: Identity
    target: ClusterTarget
    podName: PodName
    podUid: Identifier | None
    observedResourceVersion: Version | None
    phase: Literal["prepared", "launching", "observed", "terminated", "not_started", "rejected", "released"]
    state: RunState
    leaseState: Literal["held", "cleanup_pending", "released"]
    heartbeatUntil: AwareDatetime
    deadlineAt: AwareDatetime
    sandboxPhase: Literal["reserved", "creating", "created", "cleanup_pending", "released"] | None
    sandboxId: Identifier | None
    isolationProfile: Literal["kata", "gvisor-demo"] | None
    runtimeImage: Annotated[str, Field(max_length=512)] | None
    termination: PodTermination | None
    notStarted: PodNotStarted | None = None
    creationRejection: PodCreationRejection | None = None


class ClusterClaim(WireModel):
    lease: SupervisedLease | None
    state: RunState
    execution: ClusterSnapshot | None


class ClusterLookup(WireModel):
    state: RunState
    execution: ClusterSnapshot | None


class ClusterCleanupProof(WireModel):
    proofId: Identifier
    kind: Literal["never_launched", "terminated_pod", "never_started_pod", "create_rejected"]
    clusterId: Identifier
    namespaceUid: Identifier
    podName: PodName
    podUid: Identifier | None
    terminationId: Identifier | None
    sandboxId: Identifier | None
    evidenceSha256: Hash
    podAbsent: Annotated[bool, Field(strict=True)]
    sandboxAbsent: Annotated[bool, Field(strict=True)]
    creationSettled: Annotated[bool, Field(strict=True)]


class ClusterSupervisorClient:
    PREFIX = "/internal/platform/reconcile/cluster"

    def __init__(self, control: ControlPlaneClient, supervisor_id: str, target: ClusterTarget):
        if control.http.headers.get("X-Platform-Key-Id") != supervisor_id:
            raise ValueError("Cluster supervisor transport identity mismatch")
        self.control, self.supervisor_id, self.target = control, supervisor_id, target

    def _snapshot(self, value: object, lease_id: str | None = None) -> ClusterSnapshot:
        try:
            row = ClusterSnapshot.model_validate(value)
            if (row.supervisorId != self.supervisor_id or row.target != self.target
                    or row.lease.workerId != self.target.workerId or row.lease.scope.environment != self.target.environment
                    or row.podName != "oa-exec-" + row.lease.leaseId.replace("-", "")
                    or (lease_id is not None and row.lease.leaseId != lease_id)):
                raise ValueError()
            if row.phase in {"prepared", "launching"} and (row.podUid is not None or row.observedResourceVersion is not None):
                raise ValueError()
            if row.phase in {"observed", "terminated", "not_started"} and (row.podUid is None or row.observedResourceVersion is None):
                raise ValueError()
            if (row.podUid is None) != (row.observedResourceVersion is None):
                raise ValueError()
            if (row.phase == "terminated" and row.termination is None
                    or row.phase in {"prepared", "launching", "observed", "not_started"} and row.termination is not None):
                raise ValueError()
            if row.termination is not None and (row.termination.podUid != row.podUid or not row.termination.consistent()):
                raise ValueError()
            if row.phase == "not_started" and row.notStarted is None:
                raise ValueError()
            if row.notStarted is not None and (row.phase not in {"not_started", "released"} or row.termination is not None
                    or row.notStarted.podUid != row.podUid or not row.notStarted.nodeUnbound or not row.notStarted.containerStatusesEmpty
                    or row.state not in TERMINAL):
                raise ValueError()
            if row.phase == "rejected" and row.creationRejection is None:
                raise ValueError()
            if row.creationRejection is not None and (row.phase not in {"rejected", "released"}
                    or row.podUid is not None or row.termination is not None or row.notStarted is not None
                    or row.state not in TERMINAL or not row.creationRejection.consistent()
                    or row.creationRejection.podName != row.podName or row.creationRejection.namespaceUid != row.target.namespaceUid
                    or row.sandboxId is not None
                    or row.sandboxPhase not in ({None, "released"} if row.phase == "released" else {None, "reserved"})
                    or row.phase == "rejected" and row.leaseState != "cleanup_pending"):
                raise ValueError()
            if row.phase == "released":
                if row.leaseState != "released" or row.state not in TERMINAL or (row.podUid is not None and row.termination is None and row.notStarted is None):
                    raise ValueError()
            elif row.leaseState == "released":
                raise ValueError()
            return row
        except ValueError:
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID") from None

    def _run(self, run: RunRef):
        if run.environment != self.target.environment:
            raise ControlPlaneError("CONTROL_CLUSTER_INPUT_INVALID")
        return {"tenant": run.tenant, "environment": run.environment}

    async def claim(self, run: RunRef) -> ClusterClaim:
        data = await self.control.request(f"{self.PREFIX}/runs/{run.run_id}/claim", self._run(run))
        try:
            result = ClusterClaim.model_validate(data)
            if result.lease is None:
                if result.execution is not None:
                    raise ValueError()
            else:
                row = self._snapshot(result.execution)
                if (row.lease != result.lease or row.lease.attempt_lease().run != run or result.state != "running"
                        or row.phase != "prepared" or row.state != "running" or row.leaseState != "held"):
                    raise ValueError()
            return result
        except ValueError:
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID") from None

    async def _post(self, lease_id: str, operation: str, body: dict | None = None):
        lease_id = SupervisorClient._id(lease_id)
        data = await self.control.request(f"{self.PREFIX}/attempts/{lease_id}/{operation}", body or {})
        return self._snapshot(data, lease_id)

    async def _lookup(self, run: RunRef, operation: str):
        data = await self.control.request(f"{self.PREFIX}/runs/{run.run_id}/{operation}", self._run(run))
        try:
            result = ClusterLookup.model_validate(data)
            if result.execution is not None:
                row = self._snapshot(result.execution)
                if row.lease.attempt_lease().run != run or row.state != result.state:
                    raise ValueError()
            elif result.state in {"running", "cancel_requested"}:
                raise ValueError()
            return result
        except ValueError:
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID") from None

    async def lookup(self, run: RunRef):
        return await self._lookup(run, "lookup")

    async def abandon(self, run: RunRef):
        return await self._lookup(run, "abandon")

    async def inspect(self, lease_id: str):
        return await self._post(lease_id, "inspect")

    async def begin(self, lease_id: str):
        row = await self._post(lease_id, "begin")
        if row.phase != "launching" or row.state != "running" or row.leaseState != "held":
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID")
        return row

    async def observed(self, lease_id: str, pod_uid: str, resource_version: str):
        if not isinstance(resource_version, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", resource_version):
            raise ControlPlaneError("CONTROL_CLUSTER_INPUT_INVALID")
        row = await self._post(lease_id, "observed", {"podUid": SupervisorClient._id(pod_uid), "resourceVersion": resource_version})
        if row.phase != "observed" or row.podUid != pod_uid:
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID")
        return row

    async def creation_rejected(self, lease_id: str, receipt: PodCreationRejection):
        if not receipt.consistent() or receipt.namespaceUid != self.target.namespaceUid:
            raise ControlPlaneError("CONTROL_CLUSTER_INPUT_INVALID")
        row = await self._post(lease_id, "creation-rejected", receipt.model_dump(mode="json"))
        if row.phase not in {"rejected", "released"} or row.creationRejection != receipt:
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID")
        return row

    async def terminated(self, lease_id: str, termination: PodTermination):
        if not termination.consistent():
            raise ControlPlaneError("CONTROL_CLUSTER_INPUT_INVALID")
        row = await self._post(lease_id, "terminated", termination.model_dump(mode="json"))
        if row.phase not in {"terminated", "released"} or row.termination != termination:
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID")
        return row

    async def fence(self, lease_id: str):
        row = await self._post(lease_id, "fence")
        if row.state not in TERMINAL:
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID")
        return row

    async def not_started(self, lease_id: str, receipt: PodNotStarted):
        if not receipt.nodeUnbound or not receipt.containerStatusesEmpty:
            raise ControlPlaneError("CONTROL_CLUSTER_INPUT_INVALID")
        row = await self._post(lease_id, "not-started", receipt.model_dump(mode="json"))
        if row.phase not in {"not_started", "released"} or row.notStarted != receipt:
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID")
        return row

    async def adopt_sandbox(self, lease_id: str, sandbox_id: str):
        row = await self._post(lease_id, "sandbox-observed", {"sandboxId": SupervisorClient._id(sandbox_id)})
        if row.podUid is None or row.sandboxId != sandbox_id or row.phase in {"prepared", "released"}:
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID")
        return row

    async def release(self, lease_id: str, proof: ClusterCleanupProof):
        if (not (proof.podAbsent and proof.sandboxAbsent and proof.creationSettled)
                or proof.clusterId != self.target.clusterId or proof.namespaceUid != self.target.namespaceUid
                or proof.podName != "oa-exec-" + SupervisorClient._id(lease_id).replace("-", "")
                or (proof.kind == "never_launched" and (proof.podUid is not None or proof.terminationId is not None))
                or (proof.kind == "create_rejected" and (proof.podUid is not None or proof.terminationId is None))
                or (proof.kind in {"terminated_pod", "never_started_pod"} and (proof.podUid is None or proof.terminationId is None))):
            raise ControlPlaneError("CONTROL_CLUSTER_INPUT_INVALID")
        row = await self._post(lease_id, "release", proof.model_dump(mode="json"))
        if (row.phase != "released" or row.podUid != proof.podUid or row.sandboxId != proof.sandboxId
                or (row.termination.id if row.termination else row.notStarted.id if row.notStarted else row.creationRejection.id if row.creationRejection else None) != proof.terminationId
                or proof.kind == "terminated_pod" and row.termination is None
                or proof.kind == "never_started_pod" and row.notStarted is None
                or proof.kind == "create_rejected" and row.creationRejection is None):
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID")
        return row

    async def pending(self, after: str | None = None, limit: int = 100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ControlPlaneError("CONTROL_CLUSTER_INPUT_INVALID")
        data = await self.control.request(f"{self.PREFIX}/pending", {"after": SupervisorClient._id(after) if after is not None else None, "limit": limit})
        if not isinstance(data, list) or len(data) > limit:
            raise ControlPlaneError("CONTROL_CLUSTER_INVALID")
        rows = [self._snapshot(value) for value in data]
        cursor = after or ""
        for row in rows:
            if row.lease.leaseId <= cursor or row.phase == "released":
                raise ControlPlaneError("CONTROL_CLUSTER_INVALID")
            cursor = row.lease.leaseId
        return rows
