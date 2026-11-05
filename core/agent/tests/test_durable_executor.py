"""Real DeepAgents and OpenAI SDK over local TCP; model/control responses are explicit fixtures."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiohttp import web
from aiohttp.test_utils import TestServer as AiohttpServer
from common.otlp import sid
from common.otlp.trace.span import Span
import pytest

from agent.domain.models.endpoint_policy import EndpointPolicy, parse_endpoint
from agent.durable.contracts import RunRef
from agent.durable.control import AttemptLease
from agent.durable.executor import RuntimeExecutor
from agent.durable.permit import ExecutionFenced
from agent.durable.runtime_config import RuntimeBundle


@pytest.fixture
def execution(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setattr(
        sid, "sid_generator2", SimpleNamespace(gen=lambda: "execution-contract")
    )
    lease = AttemptLease(RunRef("tenant-a", "test", str(uuid4())), 1, str(uuid4()), 1)
    control = SimpleNamespace(
        permit=AsyncMock(return_value=True),
        heartbeat=AsyncMock(return_value=True),
        append=AsyncMock(),
        output=AsyncMock(),
    )
    return lease, control, Span(app_id="executor-test"), SimpleNamespace(trace=[])


def bundle(endpoint):
    return RuntimeBundle.model_validate(
        {
            "input": {"message": "synthetic user message"},
            "snapshot": {
                "execution": {"instructions": "Answer briefly", "maxTokens": 32}
            },
            "deadline": datetime.now(timezone.utc) + timedelta(minutes=1),
            "skills": [],
            "model": {
                "endpoint": endpoint,
                "modelName": "contract-text",
                "credential": "synthetic-executor-key",
                "version": 1,
                "supportsTools": True,
                "supportsUsage": True,
                "connectTimeoutSeconds": 2,
                "readTimeoutSeconds": 5,
                "totalTimeoutSeconds": 45,
                "caRef": None,
            },
        }
    )


def policy(endpoint):
    return EndpointPolicy(
        frozenset({parse_endpoint(endpoint)[1]}), (), allow_loopback_for_tests=True
    )


def completion():
    def chunk(delta, finish=None):
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": "contract-text",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    events = [
        chunk({"role": "assistant"}),
        chunk({"content": "Completed through DeepAgents"}),
        chunk({}, "stop"),
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": "contract-text",
            "choices": [],
            "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
        },
    ]
    return web.Response(
        text="".join("data: " + json.dumps(event) + "\n\n" for event in events)
        + "data: [DONE]\n\n",
        content_type="text/event-stream",
    )


@pytest.mark.asyncio
async def test_actual_engine_streams_events_and_usage_through_approved_fenced_transport(
    execution,
):
    lease, control, span, trace = execution
    requests = []

    async def respond(request):
        assert request.headers["Authorization"] == "Bearer synthetic-executor-key"
        requests.append(await request.json())
        return completion()

    app = web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    async with AiohttpServer(app) as server:
        endpoint = str(server.make_url("/v1"))
        result = await RuntimeExecutor(control, policy=policy(endpoint)).execute(
            lease, bundle(endpoint), span, trace
        )
    assert result == {"prompt_tokens": 22, "completion_tokens": 10, "total_tokens": 32}
    assert len(requests) == 2 and requests[0]["stream"] is True
    names={tool["function"]["name"] for tool in requests[0]["tools"]}
    assert "write_file" in names and "execute" not in names and "task" not in names
    assert not requests[1].get("tools")
    calls = control.append.await_args_list
    assert (
        "".join(
            call.args[3]["text"] for call in calls if call.args[2] == "message.delta"
        )
        == "Completed through DeepAgents"
    )
    assert len({call.args[1] for call in calls}) == len(calls)
    assert control.permit.await_count >= 4
    assert "synthetic-executor-key" not in repr(calls)


@pytest.mark.asyncio
async def test_revoked_lease_cannot_reach_the_model(execution):
    lease, control, span, trace = execution
    control.permit.return_value = False
    endpoint = "http://127.0.0.1:34567/v1"
    with pytest.raises(ExecutionFenced):
        await RuntimeExecutor(control, policy=policy(endpoint)).execute(
            lease, bundle(endpoint), span, trace
        )
    control.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_untrusted_input_cannot_supply_connection_or_host_execution_configuration(
    execution,
):
    lease, control, span, trace = execution
    endpoint = "http://127.0.0.1:34567/v1"
    config = bundle(endpoint)
    config.input["base_url"] = "https://private-value-should-not-be-echoed.invalid"
    with pytest.raises(RuntimeError, match="execution_configuration_invalid") as caught:
        await RuntimeExecutor(control, policy=policy(endpoint)).execute(
            lease, config, span, trace
        )
    assert "private-value" not in str(caught.value)
    control.permit.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_cancels_actual_http_call_without_retry(execution):
    lease, control, span, trace = execution
    started, unblock = asyncio.Event(), asyncio.Event()
    calls = 0

    async def respond(request):
        nonlocal calls
        calls += 1
        started.set()
        await unblock.wait()
        return completion()

    app = web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    async with AiohttpServer(app) as server:
        endpoint = str(server.make_url("/v1"))
        config = bundle(endpoint)
        config = config.model_copy(
            update={"model": config.model.model_copy(update={"totalTimeoutSeconds": 1})}
        )
        try:
            with pytest.raises(TimeoutError):
                await RuntimeExecutor(control, policy=policy(endpoint)).execute(
                    lease, config, span, trace
                )
        finally:
            unblock.set()
    assert calls == 1 and started.is_set()
    assert not any(call.args[2] == "usage" for call in control.append.await_args_list)


@pytest.mark.asyncio
async def test_skills_cannot_silently_fall_back_to_host_or_unbound_backend(execution):
    lease, control, span, trace = execution
    endpoint = "http://127.0.0.1:34567/v1"
    value = bundle(endpoint).model_dump()
    value["skills"] = [{"releaseId": str(uuid4()), "manifestSha256": "a" * 64}]
    config = RuntimeBundle.model_validate(value)
    with pytest.raises(RuntimeError, match="execution_configuration_invalid"):
        await RuntimeExecutor(control, policy=policy(endpoint)).execute(
            lease, config, span, trace
        )
    control.permit.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_deepagents_state_file_is_archived_before_final_answer(execution):
    lease, control, span, trace = execution
    calls=0;archived=[]
    async def save(bound,descriptor,content):
        assert bound==lease
        archived.append((descriptor,b"".join([chunk async for chunk in content])))
        return {"state":"available"}
    control.output=save
    async def respond(request):
        nonlocal calls
        calls+=1
        body=await request.json()
        if calls==1:
            delta={"role":"assistant","tool_calls":[{"index":0,"id":"output-write-1","type":"function","function":{"name":"write_file","arguments":json.dumps({"file_path":"/workspace/outputs/report.csv","content":"项目,结果\nA,完成"})}}]}
            chunk={"id":"file-model","object":"chat.completion.chunk","created":1700000000,"model":"contract-text","choices":[{"index":0,"delta":delta,"finish_reason":"tool_calls"}]}
            return web.Response(text="data: "+json.dumps(chunk)+"\n\ndata: [DONE]\n\n",content_type="text/event-stream")
        assert archived, "Completed file must be durable before the next model call"
        return completion()
    app=web.Application();app.router.add_post("/v1/chat/completions",respond)
    async with AiohttpServer(app) as server:
        endpoint=str(server.make_url("/v1"))
        await RuntimeExecutor(control,policy=policy(endpoint)).execute(lease,bundle(endpoint),span,trace)
    assert len(archived)==1
    descriptor,data=archived[0]
    assert data=="项目,结果\nA,完成".encode()
    assert descriptor["step"]=="tool:output-write-1"
    assert descriptor["path"]=="/workspace/outputs/report.csv"
