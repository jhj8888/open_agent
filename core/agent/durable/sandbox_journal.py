"""Attempt-bound private journal. Worker destroy acknowledgements never release quota."""

from __future__ import annotations

from uuid import UUID

from agent.durable.control import AttemptLease, ControlPlaneClient, ControlPlaneError
from agent.platform_runtime.sandbox import RunScope


class DurableSandboxJournal:
    def __init__(self, control: ControlPlaneClient, lease: AttemptLease) -> None:
        self.control, self.lease = control, lease

    def _check_scope(self, scope: RunScope) -> None:
        if (
            scope.tenant_id != self.lease.run.tenant
            or scope.environment != self.lease.run.environment
            or str(scope.run_id) != self.lease.run.run_id
            or scope.attempt != self.lease.attempt
            or str(scope.lease_id) != self.lease.lease_id
        ):
            raise ControlPlaneError("CONTROL_SANDBOX_SCOPE_INVALID")

    async def _post(self, operation: str, body: dict) -> None:
        row = await self.control.request(
            f"/internal/platform/worker/runs/{self.lease.run.run_id}/sandbox/{operation}",
            body,
        )
        if not isinstance(row, dict) or row.get("accepted") is not True:
            raise ControlPlaneError("CONTROL_SANDBOX_ACK_INVALID")

    async def reserve(self, scope: RunScope, profile: str, image: str) -> None:
        self._check_scope(scope)
        await self._post(
            "reserve", {"lease": self.lease.body(), "profile": profile, "image": image}
        )

    async def creating(self, scope: RunScope) -> None:
        self._check_scope(scope)
        await self._post("creating", self.lease.body())

    async def _report(self, scope: RunScope, sandbox_id: str, operation: str) -> None:
        self._check_scope(scope)
        try:
            if str(UUID(sandbox_id)) != sandbox_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ControlPlaneError("CONTROL_SANDBOX_ID_INVALID") from None
        await self._post(operation, {"lease": self.lease.body(), "sandboxId": sandbox_id})

    async def created(self, scope: RunScope, sandbox_id: str) -> None:
        await self._report(scope, sandbox_id, "created")

    async def cleanup_pending(self, scope: RunScope, sandbox_id: str) -> None:
        await self._report(scope, sandbox_id, "cleanup-pending")

    async def released(self, scope: RunScope, sandbox_id: str) -> None:
        # Historical provider callback name means SDK destroy returned; the independent
        # verifier alone may release the SQL reservation and execution slot.
        await self._report(scope, sandbox_id, "destruction-reported")
