"""Verify fixture behavior through the official SDK and a real local TCP server."""
import asyncio
import importlib.util
import json
from pathlib import Path
import socket

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, AuthenticationError, RateLimitError
import pytest
import pytest_asyncio
import uvicorn

spec = importlib.util.spec_from_file_location("model_contract_server", Path(__file__).with_name("server.py"))
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)


@pytest_asyncio.fixture
async def endpoint():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(fixture_module.app, log_level="critical", lifespan="off", timeout_graceful_shutdown=1))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 4)
        listener.close()


def client(endpoint, **options):
    return AsyncOpenAI(base_url=endpoint, api_key=fixture_module.FIXTURE_TOKEN, max_retries=0,
                       http_client=httpx.AsyncClient(trust_env=False, follow_redirects=False), **options)


@pytest.mark.asyncio
async def test_text_and_token_usage(endpoint):
    async with client(endpoint) as sdk:
        result = await sdk.chat.completions.create(model="contract-text", messages=[{"role": "user", "content": "test"}])
        assert result.choices[0].message.content == "Contract response"
        assert result.usage.total_tokens == 18


@pytest.mark.asyncio
async def test_stream_chunks_and_final_usage(endpoint):
    async with client(endpoint) as sdk:
        stream = await sdk.chat.completions.create(model="contract-text", messages=[], stream=True, stream_options={"include_usage": True})
        parts = [part async for part in stream]
        assert "".join(choice.delta.content or "" for part in parts for choice in part.choices) == "Contract response"
        assert parts[-1].usage.total_tokens == 18


@pytest.mark.asyncio
async def test_tool_arguments_and_result_round_trip(endpoint):
    tool = {"type": "function", "function": {"name": "add_numbers", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}}}
    async with client(endpoint) as sdk:
        first = await sdk.chat.completions.create(model="contract-tools", messages=[], tools=[tool])
        call = first.choices[0].message.tool_calls[0]
        assert json.loads(call.function.arguments) == {"a": 2, "b": 3}
        final = await sdk.chat.completions.create(model="contract-tools", messages=[first.choices[0].message.model_dump(exclude_none=True), {"role": "tool", "tool_call_id": call.id, "content": "5"}], tools=[tool])
        assert final.choices[0].message.content == "5"


@pytest.mark.asyncio
async def test_fragmented_streaming_tool_arguments(endpoint):
    async with client(endpoint) as sdk:
        stream = await sdk.chat.completions.create(model="contract-tools", messages=[], stream=True)
        arguments = ""
        async for part in stream:
            for choice in part.choices:
                for call in choice.delta.tool_calls or []:
                    arguments += call.function.arguments or ""
        assert json.loads(arguments) == {"a": 2, "b": 3}


@pytest.mark.asyncio
async def test_missing_usage_stays_unknown(endpoint):
    async with client(endpoint) as sdk:
        result = await sdk.chat.completions.create(model="contract-no-usage", messages=[])
        assert result.usage is None


@pytest.mark.asyncio
@pytest.mark.parametrize("model,exception", [("contract-unauthorized", AuthenticationError), ("contract-rate-limit", RateLimitError)])
async def test_errors_do_not_retry_accepted_requests(endpoint, model, exception):
    before = fixture_module.app.state.calls
    async with client(endpoint) as sdk:
        with pytest.raises(exception):
            await sdk.chat.completions.create(model=model, messages=[])
    assert fixture_module.app.state.calls == before + 1


@pytest.mark.asyncio
async def test_timeout(endpoint):
    async with client(endpoint, timeout=0.05) as sdk:
        with pytest.raises(APITimeoutError):
            await sdk.chat.completions.create(model="contract-timeout", messages=[])


@pytest.mark.asyncio
async def test_malformed_sse_is_an_error(endpoint):
    async with client(endpoint) as sdk:
        stream = await sdk.chat.completions.create(model="contract-malformed-sse", messages=[], stream=True)
        with pytest.raises(json.JSONDecodeError):
            _ = [part async for part in stream]


@pytest.mark.asyncio
async def test_midstream_disconnect_is_not_success(endpoint):
    async with client(endpoint) as sdk:
        stream = await sdk.chat.completions.create(model="contract-disconnect", messages=[], stream=True)
        with pytest.raises((httpx.RemoteProtocolError, APIConnectionError)):
            _ = [part async for part in stream]
