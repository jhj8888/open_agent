import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from agent.durable.contracts import RunRef
from agent.durable.control import ControlPlaneClient, ControlPlaneError, AttemptLease


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://control.internal",
        "https://user:pass@control.internal",
        "https://control.internal/base",
        "https://control.internal?token=x",
    ],
)
def test_control_service_credentials_require_tls_or_literal_loopback(endpoint):
    with pytest.raises(ValueError):
        ControlPlaneClient(endpoint, "worker", "k" * 32)


@pytest.mark.asyncio
async def test_client_sends_service_identity_but_never_accepts_worker_identity_from_body():
    run = RunRef("tenant", "test", str(uuid4()))
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"lease": {"attempt": 1, "leaseId": str(uuid4()), "fencingToken": 1}},
        )

    client = ControlPlaneClient(
        "https://control.internal",
        "worker",
        "k" * 32,
        transport=httpx.MockTransport(respond),
    )
    try:
        assert (await client.claim(run)).attempt == 1
        assert requests[0].headers["X-Platform-Key-Id"] == "worker"
        assert "workerId" not in json.loads(requests[0].content)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 409, 503])
async def test_errors_never_echo_private_response_or_follow_redirects(status):
    def respond(request):
        return httpx.Response(
            status,
            text="sensitive-model-key",
            headers={"Location": "https://attacker.invalid"},
        )

    client = ControlPlaneClient(
        "https://control.internal",
        "worker",
        "k" * 32,
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(ControlPlaneError) as caught:
            await client.request("/internal/platform/worker/status", {})
        assert str(caught.value) == f"CONTROL_HTTP_{status}"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_client_bounds_response_and_rejects_paths_outside_internal_namespace():
    client = ControlPlaneClient(
        "https://control.internal",
        "worker",
        "k" * 32,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 100)
        ),
    )
    try:
        with pytest.raises(ControlPlaneError, match="CONTROL_RESPONSE_LIMIT"):
            await client.request("/internal/platform/worker/status", {}, limit=10)
        with pytest.raises(ControlPlaneError, match="CONTROL_PATH_INVALID"):
            await client.request("https://attacker.invalid", {})
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_slow_stream_cannot_extend_outbox_claim_indefinitely():
    class SlowBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.01)
                yield b" "

        async def aclose(self):
            self.closed = True

    body = SlowBody()
    client = ControlPlaneClient(
        "https://control.internal",
        "worker",
        "k" * 32,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=body)),
    )
    client.TOTAL_TIMEOUT_SECONDS = 0.05
    try:
        with pytest.raises(ControlPlaneError, match="CONTROL_UNAVAILABLE"):
            await asyncio.wait_for(
                client.request("/internal/platform/dispatch/claim", {}), timeout=1
            )
        assert body.closed
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_runtime_secret_is_masked_and_invalid_runtime_errors_do_not_echo_it():
    run = RunRef("tenant", "test", str(uuid4()))
    lease = AttemptLease(run, 1, str(uuid4()), 1)
    data = {
        "input": {"message": "synthetic"},
        "snapshot": {},
        "deadline": "2026-11-04T12:00:00Z",
        "skills": [],
        "model": {
            "endpoint": "https://model.invalid/v1",
            "modelName": "synthetic",
            "credential": "never-serialize-this",
            "version": 1,
            "supportsTools": True,
            "supportsUsage": True,
            "connectTimeoutSeconds": 5,
            "readTimeoutSeconds": 60,
            "totalTimeoutSeconds": 120,
            "caRef": None,
        },
    }
    client = ControlPlaneClient(
        "https://control.internal",
        "worker",
        "k" * 32,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data)),
    )
    try:
        bundle = await client.runtime(lease)
        assert bundle.model.credential.get_secret_value() == "never-serialize-this"
        assert (
            "never-serialize-this"
            not in repr(bundle) + repr(bundle.model) + bundle.model_dump_json()
        )
        data["model"]["version"] = -1
        with pytest.raises(
            ControlPlaneError, match="CONTROL_RUNTIME_INVALID"
        ) as caught:
            await client.runtime(lease)
        assert "never-serialize-this" not in str(caught.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_artifact_transport_preserves_exact_bytes_and_bounds_download():
    run = RunRef("tenant", "test", str(uuid4()))
    lease = AttemptLease(run, 1, str(uuid4()), 1)
    release = str(uuid4())
    content = b"exact\nmanifest\n\xff"
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=content)

    client = ControlPlaneClient(
        "https://control.internal",
        "worker",
        "k" * 32,
        transport=httpx.MockTransport(respond),
    )
    try:
        assert await client.artifact(lease, release, -1, 1024) == content
        assert (
            seen[0]["lease"]["leaseId"] == lease.lease_id
            and seen[0]["releaseId"] == release
        )
        with pytest.raises(ControlPlaneError, match="CONTROL_RESPONSE_LIMIT"):
            await client.artifact(lease, release, 0, 2)
    finally:
        await client.aclose()
