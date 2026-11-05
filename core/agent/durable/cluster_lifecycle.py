"""Join the durable control protocol to real Kubernetes process operations.

No model/tool replay and no quota release here: the outer supervisor must also
independently reconcile the separate untrusted Skill sandbox before release.
"""
from __future__ import annotations

from agent.durable.cluster_control import ClusterSnapshot, ClusterSupervisorClient
from agent.durable.cluster_process import ClusterProcessError, KubernetesProcesses


class ClusterLifecycle:
    def __init__(self, control: ClusterSupervisorClient, processes: KubernetesProcesses):
        if control.target != processes.target:
            raise ClusterProcessError("CLUSTER_BINDING_INVALID")
        self.control, self.processes = control, processes

    async def launch(self, initial: ClusterSnapshot) -> ClusterSnapshot:
        # A begin response loss leaves launching in the DB. Never retry begin or
        # create; recovery only inspects the recorded deterministic Pod name.
        current = await self.control.begin(initial.lease.leaseId)
        pod = await self.processes.launch(current)
        current = await self.control.observed(current.lease.leaseId, pod.metadata.uid, pod.metadata.resource_version)
        await self.processes.activate(current)
        return current

    async def stop_once(self, initial: ClusterSnapshot) -> tuple[ClusterSnapshot, bool]:
        """Bounded recovery pass. False retains resources for the next scanner pass."""
        lease = initial.lease.leaseId
        current = await self.control.inspect(lease)
        if current.phase == "released":
            return current, True
        current = await self.control.fence(lease)
        if current.phase in {"terminated", "not_started"}:
            await self.processes.finalize(current)
            return current, await self.processes.absent_after_receipt(current)
        pod = await self.processes.inspect(current)
        if current.phase == "prepared":
            if pod is not None:
                raise ClusterProcessError("CLUSTER_LAUNCH_CONFLICT")
            # The one-shot begin was never issued; fencing makes it impossible.
            return current, True
        if pod is None:
            raise ClusterProcessError("CLUSTER_LAUNCH_UNCONFIRMED")
        if current.phase == "launching":
            current = await self.control.observed(lease, pod.metadata.uid, pod.metadata.resource_version)
        if current.phase != "observed":
            raise ClusterProcessError("CLUSTER_BINDING_INVALID")
        if self.processes.unbound_unstarted(current, pod):
            if pod.metadata.deletion_timestamp is None:
                await self.processes.request_stop(current, pod)
                return current, False
            receipt = self.processes.not_started_receipt(current, pod)
            current = await self.control.not_started(lease, receipt)
            await self.processes.finalize(current)
            return current, await self.processes.absent_after_receipt(current)
        if pod.status is None or pod.status.phase not in {"Succeeded", "Failed"}:
            await self.processes.request_stop(current, pod)
            return current, False
        receipt = await self.processes.termination(current, pod)
        # This is the durable boundary. If it fails, leave the finalizer intact.
        current = await self.control.terminated(lease, receipt)
        await self.processes.finalize(current)
        return current, await self.processes.absent_after_receipt(current)
