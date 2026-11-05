import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from agent.durable.contracts import RunRef
from agent.durable.control import ControlPlaneClient, ControlPlaneError, AttemptLease


@pytest.mark.asyncio
async def test_observation_transport_retries_identical_body_and_does_not_replay_execution():
    requests=[]
    def respond(request):
        requests.append(request.content)
        return httpx.Response(503 if len(requests)<3 else 200,json={"accepted":True})
    client=ControlPlaneClient("https://control.internal","worker","k"*32,transport=httpx.MockTransport(respond))
    lease=AttemptLease(RunRef("tenant","test",str(uuid4())),1,str(uuid4()),1)
    try:
        await client.observation(lease,"finish",{"stepId":"call-1","inputTokens":12})
        assert len(requests)==3 and len(set(requests))==1
    finally:
        await client.aclose()


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


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['recovered', 'twice', 'fenced', 'http_denied', 'bad_json', 'cancelled'])
async def test_runtime_retry_is_bounded_and_rechecks_lease(scenario, capsys):
    lease = AttemptLease(RunRef('tenant', 'test', str(uuid4())), 1, str(uuid4()), 1)
    calls = []
    secret = 'private-response-must-not-leak'
    bundle = {
        'input': {'message': 'test'}, 'snapshot': {}, 'skills': [], 'deadline': '2026-11-05T12:00:00Z',
        'model': {'endpoint': 'https://model.invalid/v1', 'modelName': 'test', 'credential': secret,
                  'version': 1, 'supportsTools': True, 'supportsUsage': True,
                  'connectTimeoutSeconds': 5, 'readTimeoutSeconds': 60, 'totalTimeoutSeconds': 120, 'caRef': None},
    }

    def respond(request):
        calls.append(request.url.path.rsplit('/', 1)[-1])
        assert json.loads(request.content) == lease.body()
        if calls[-1] == 'heartbeat':
            return httpx.Response(200, json={'permitted': scenario != 'fenced'})
        if scenario == 'http_denied':
            return httpx.Response(403, text=secret)
        if scenario == 'bad_json':
            return httpx.Response(200, text=secret)
        if scenario == 'cancelled':
            raise asyncio.CancelledError()
        if calls.count('runtime') == 1 or scenario == 'twice':
            raise httpx.ReadTimeout(secret, request=request)
        return httpx.Response(200, json=bundle)

    client = ControlPlaneClient('https://control.internal', 'worker', 'k' * 32, transport=httpx.MockTransport(respond))
    try:
        if scenario == 'recovered':
            assert (await client.runtime(lease)).model.credential.get_secret_value() == secret
            assert calls == ['runtime', 'heartbeat', 'runtime']
        elif scenario == 'cancelled':
            with pytest.raises(asyncio.CancelledError):
                await client.runtime(lease)
            assert calls == ['runtime']
        else:
            with pytest.raises(ControlPlaneError) as caught:
                await client.runtime(lease)
            assert secret not in str(caught.value)
            if scenario == 'fenced':
                assert caught.value.code == 'CONTROL_LEASE_FENCED'
                assert calls == ['runtime', 'heartbeat']
            elif scenario == 'twice':
                assert caught.value.category == 'read_timeout'
                assert calls == ['runtime', 'heartbeat', 'runtime']
            else:
                assert calls == ['runtime']
        assert secret not in capsys.readouterr().out
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_mutation_transport_failure_is_never_retried():
    lease = AttemptLease(RunRef('tenant', 'test', str(uuid4())), 1, str(uuid4()), 1)
    calls = []

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout('private response', request=request)

    client = ControlPlaneClient('https://control.internal', 'worker', 'k' * 32, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ControlPlaneError, match='CONTROL_UNAVAILABLE'):
            await client.finish(lease, 'succeeded')
        assert len(calls) == 1
    finally:
        await client.aclose()
