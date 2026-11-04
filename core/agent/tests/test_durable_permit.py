import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from agent.durable.contracts import RunRef
from agent.durable.control import AttemptLease
from agent.durable.permit import ExecutionFenced, ExecutionPermit, PermitTransport


def setup():
    lease = AttemptLease(RunRef("tenant", "test", str(uuid4())), 1, str(uuid4()), 1)
    control = SimpleNamespace(
        permit=AsyncMock(return_value=True), heartbeat=AsyncMock(return_value=True)
    )
    guard = ExecutionPermit(
        control, lease, datetime.now(timezone.utc) + timedelta(seconds=60)
    )
    return control, guard


@pytest.mark.asyncio
async def test_heartbeat_loss_cancels_inflight_work_and_closes_late_operations():
    control, guard = setup()
    started, stopped = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(guard.run(work, interval=0.02))
    await started.wait()
    control.heartbeat.return_value = False
    with pytest.raises(ExecutionFenced):
        await asyncio.wait_for(task, 1)
    assert stopped.is_set()
    count = control.permit.await_count
    with pytest.raises(ExecutionFenced):
        await guard.check()
    assert control.permit.await_count == count


@pytest.mark.asyncio
async def test_caller_cancel_waits_for_cleanup_and_never_keeps_monitor_running():
    control, guard = setup()
    started, stopped = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)
            stopped.set()

    task = asyncio.create_task(guard.run(work, interval=0.02))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set() and guard.closed
    count = control.heartbeat.await_count
    await asyncio.sleep(0.04)
    assert control.heartbeat.await_count == count


@pytest.mark.asyncio
async def test_every_model_request_requires_current_permission_and_network_failure_is_closed():
    control, guard = setup()
    sent = []

    def respond(request):
        sent.append(request.url.path)
        return httpx.Response(200, content=b"ok")

    async with httpx.AsyncClient(
        transport=PermitTransport(guard, httpx.MockTransport(respond))
    ) as client:
        assert (
            await client.post("https://model.invalid/chat/completions")
        ).text == "ok"
        control.permit.side_effect = RuntimeError("credential-must-not-escape")
        with pytest.raises(ExecutionFenced) as caught:
            await client.post("https://model.invalid/chat/completions")
        assert "credential" not in str(caught.value)
    assert len(sent) == 1 and guard.closed


@pytest.mark.asyncio
async def test_stream_closes_when_deadline_or_local_revocation_is_observed():
    control, guard = setup()

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"first"
            guard.closed = True
            yield b"must-not-be-returned"

        async def aclose(self):
            self.closed = True

    source = Stream()
    async with httpx.AsyncClient(
        transport=PermitTransport(
            guard,
            httpx.MockTransport(lambda request: httpx.Response(200, stream=source)),
        )
    ) as client:
        async with client.stream(
            "POST", "https://model.invalid/chat/completions"
        ) as response:
            iterator = response.aiter_bytes()
            assert await anext(iterator) == b"first"
            with pytest.raises(ExecutionFenced):
                await anext(iterator)
    assert source.closed


@pytest.mark.asyncio
async def test_expired_deadline_performs_no_control_or_model_operation():
    control, guard = setup()
    guard.deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ExecutionFenced):
        await guard.run(AsyncMock())
    control.permit.assert_not_awaited()
