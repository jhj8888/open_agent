"""Execution admission checks shared by every model request and sandbox operation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar

import httpx

from agent.durable.control import AttemptLease, ControlPlaneClient

T = TypeVar("T")


class ExecutionFenced(RuntimeError):
    def __init__(self):
        super().__init__("execution_fenced")


class ExecutionPermit:
    def __init__(
        self, control: ControlPlaneClient, lease: AttemptLease, deadline: datetime
    ):
        if deadline.tzinfo is None:
            raise ValueError("Execution deadline must have a timezone")
        self.control, self.lease, self.deadline = control, lease, deadline
        self.closed = False

    def local_check(self) -> None:
        if self.closed or datetime.now(timezone.utc) >= self.deadline:
            self.closed = True
            raise ExecutionFenced()

    async def check(self) -> None:
        self.local_check()
        try:
            allowed = await self.control.permit(self.lease)
        except Exception:
            self.closed = True
            raise ExecutionFenced() from None
        if not allowed:
            self.closed = True
        self.local_check()

    async def monitor(self, heartbeat: Callable[[], None], interval: float) -> None:
        while True:
            try:
                if not await self.control.heartbeat(self.lease):
                    raise ExecutionFenced()
                await self.check()  # Heartbeat renewal alone does not check version revocation.
                heartbeat()  # Official Temporal activity heartbeat, never a secret payload.
            except Exception:
                self.closed = True
                raise ExecutionFenced() from None
            await asyncio.sleep(interval)

    async def run(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        heartbeat: Callable[[], None] = lambda: None,
        interval: float = 5,
    ) -> T:
        if not 0 < interval <= 5:
            raise ValueError("Invalid heartbeat interval")
        await self.check()
        work = asyncio.create_task(operation())
        monitor = asyncio.create_task(self.monitor(heartbeat, interval))
        try:
            finished, _ = await asyncio.wait(
                (work, monitor), return_when=asyncio.FIRST_COMPLETED
            )
            if monitor in finished:
                # Fail closed even when work and a lost lease finish simultaneously.
                await monitor
                raise ExecutionFenced()
            result = await work
            await self.check()
            return result
        finally:
            self.closed = True
            monitor.cancel()
            if not work.done():
                work.cancel()
            await asyncio.gather(work, monitor, return_exceptions=True)


class PermitTransport(httpx.AsyncBaseTransport):
    """Wrap the approved/pinned model transport, including framework compression calls."""

    def __init__(self, permit: ExecutionPermit, inner: httpx.AsyncBaseTransport, *, model_budget=None):
        self.permit, self.inner = permit, inner
        self.model_budget = model_budget

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await self.permit.check()
        if self.model_budget is not None:
            await self.model_budget.authorize(request)
            self.permit.local_check()
        response = await self.inner.handle_async_request(request)
        try:
            self.permit.local_check()
        except ExecutionFenced:
            await response.aclose()
            raise
        response.stream = PermitStream(self.permit, response.stream)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


class PermitStream(httpx.AsyncByteStream):
    def __init__(self, permit: ExecutionPermit, stream: httpx.AsyncByteStream):
        self.permit, self.stream = permit, stream

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                self.permit.local_check()
                yield chunk
        finally:
            await self.stream.aclose()

    async def aclose(self) -> None:
        await self.stream.aclose()
