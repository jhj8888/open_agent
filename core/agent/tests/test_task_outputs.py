import hashlib
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from agent.durable.contracts import RunRef
from agent.durable.control import AttemptLease, ControlPlaneClient
from agent.platform_runtime.outputs import state_bytes
from agent.platform_runtime.sandbox import SandboxFailure


@pytest.mark.asyncio
async def test_output_stream_is_separate_from_json_logs_and_commit_is_idempotent():
    content = b"x" * (1024 * 1024 + 17)
    lease = AttemptLease(RunRef("tenant-a", "test", str(uuid4())), 1, str(uuid4()), 7)
    identifier = str(uuid4())
    calls = []
    published = False

    async def handle(request):
        nonlocal published
        calls.append(request)
        if request.url.path.endswith("/content"):
            assert request.headers["X-Output-Lease"] == lease.lease_id
            assert request.headers["Content-Type"] == "application/octet-stream"
            assert await request.aread() == content
            published = True
        return httpx.Response(
            200,
            json={
                "output_id": identifier,
                "state": "available" if published else "pending",
            },
        )

    client = ControlPlaneClient(
        "http://127.0.0.1:8000",
        "worker",
        "s" * 32,
        transport=httpx.MockTransport(handle),
    )
    descriptor = {
        "path": "/workspace/outputs/report.bin",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "step": "tool:1",
    }

    async def chunks():
        for i in range(0, len(content), 65536):
            yield content[i : i + 65536]

    try:
        assert (await client.output(lease, descriptor, chunks()))[
            "state"
        ] == "available"
        await client.output(lease, descriptor, chunks())
        assert (
            len(calls) == 3
        )  # Second reserve discovers publication; no duplicate byte upload.
        assert json.loads(calls[0].content)["lease"] == lease.body()
    finally:
        await client.http.aclose()


def test_state_file_encoding_and_limits():
    assert (
        state_bytes({"content": "current format", "encoding": "utf-8"})
        == b"current format"
    )
    assert (
        state_bytes({"content": ["第一行", "second"], "encoding": "utf-8"})
        == "第一行\nsecond".encode()
    )
    assert state_bytes({"content": ["AAEC/w=="], "encoding": "base64"}) == bytes(
        [0, 1, 2, 255]
    )
    with pytest.raises(SandboxFailure):
        state_bytes({"content": ["x"], "encoding": "unsafe"})
