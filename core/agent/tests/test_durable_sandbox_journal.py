import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import httpx
import pytest

from agent.durable.contracts import RunRef
from agent.durable.control import AttemptLease, ControlPlaneClient, ControlPlaneError
from agent.durable.sandbox_journal import DurableSandboxJournal
from agent.platform_runtime.sandbox import SandboxFailure
from agent.tests.test_platform_sandbox import provider, scope, IMAGE


def bound():
    run = scope()
    lease = AttemptLease(
        RunRef(run.tenant_id, run.environment, str(run.run_id)),
        run.attempt, str(run.lease_id), 7,
    )
    return run, lease


@pytest.mark.asyncio
async def test_journal_sends_only_bound_lease_and_worker_destruction_is_not_release():
    run, lease = bound()
    calls = []

    def respond(request):
        calls.append((request.url.path, json.loads(request.content), dict(request.headers)))
        return httpx.Response(200, json={"accepted": True})

    control = ControlPlaneClient("https://control.internal", "worker-one", "k" * 32,
                                 transport=httpx.MockTransport(respond))
    try:
        journal = DurableSandboxJournal(control, lease)
        sandbox_id = str(uuid4())
        await journal.reserve(run, "gvisor-demo", IMAGE)
        await journal.creating(run)
        await journal.created(run, sandbox_id)
        # Cleanup must still report after the local deadline, without reopening permission.
        expired = replace(run, deadline=datetime.now(timezone.utc) - timedelta(seconds=1))
        await journal.cleanup_pending(expired, sandbox_id)
        await journal.released(expired, sandbox_id)
        assert [path.rsplit("/", 1)[-1] for path, _, _ in calls] == [
            "reserve", "creating", "created", "cleanup-pending", "destruction-reported"
        ]
        assert calls[1][1] == lease.body()
        assert all(body.get("lease", body) == lease.body() for _, body, _ in calls)
        assert all(headers["x-platform-key-id"] == "worker-one" for _, _, headers in calls)
    finally:
        await control.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("tenant_id", "tenant-b"), ("environment", "prod"), ("run_id", uuid4()),
    ("attempt", 2), ("lease_id", uuid4()),
])
async def test_foreign_scope_never_reaches_control_plane(field, value):
    run, lease = bound()
    calls = []
    control = ControlPlaneClient("https://control.internal", "worker", "k" * 32,
        transport=httpx.MockTransport(lambda request: calls.append(request)))
    try:
        journal = DurableSandboxJournal(control, lease)
        with pytest.raises(ControlPlaneError, match="CONTROL_SANDBOX_SCOPE_INVALID"):
            await journal.released(replace(run, **{field: value}), str(uuid4()))
        assert not calls
    finally:
        await control.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [{"accepted": False}, {"accepted": "true"}, None])
async def test_invalid_ack_does_not_authorize_creation(reply):
    run, lease = bound()
    control = ControlPlaneClient("https://control.internal", "worker", "k" * 32,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=json.dumps(reply))))
    try:
        with pytest.raises(ControlPlaneError, match="CONTROL_SANDBOX_ACK_INVALID"):
            await DurableSandboxJournal(control, lease).creating(run)
    finally:
        await control.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["reserve", "creating"])
async def test_no_external_create_after_quota_denial_or_ambiguous_intent_ack(step):
    adapter, sandbox, journal, admission, factory, release = await provider()
    getattr(journal, step).side_effect = ControlPlaneError("CONTROL_UNAVAILABLE")
    with pytest.raises(ControlPlaneError):
        await adapter.create(scope(), [release])
    factory.assert_not_awaited()
    journal.released.assert_not_awaited()


@pytest.mark.asyncio
async def test_intent_is_persisted_before_external_create_and_ambiguous_create_keeps_reservation():
    adapter, sandbox, journal, admission, factory, release = await provider()
    order = []
    journal.reserve.side_effect = lambda *args: order.append("reserved")
    journal.creating.side_effect = lambda *args: order.append("creating")

    async def lost(*args, **kwargs):
        order.append("external-create")
        raise TimeoutError()

    factory.side_effect = lost
    with pytest.raises(SandboxFailure, match="SANDBOX_CREATE_FAILED"):
        await adapter.create(scope(), [release])
    assert order == ["reserved", "creating", "external-create"]
    factory.assert_awaited_once()
    journal.created.assert_not_awaited()
    journal.released.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_after_intent_does_not_allocate_or_free_quota():
    adapter, sandbox, journal, admission, factory, release = await provider()
    journal.creating.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await adapter.create(scope(), [release])
    factory.assert_not_awaited()
    journal.released.assert_not_awaited()
