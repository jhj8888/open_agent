"""Official SDK streaming + subsequent tool-history transport, using explicit fixtures."""

import json
from unittest.mock import AsyncMock

import httpx
from langchain_core.messages import HumanMessage, ToolMessage
import pytest

from agent.domain.models.providers import create_provider_model
from agent.engine.observations import ExecutionObservations


@pytest.mark.asyncio
async def test_official_deepseek_stream_preserves_reasoning_through_tool_roundtrip():
    requests = []

    async def transport(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.host == "deepseek.invalid"
        assert body["thinking"] == {"type": "enabled"}
        first = len(requests) == 1
        deltas = [
            {"role": "assistant", "reasoning_content": "synthetic reasoning "},
            {"reasoning_content": "kept in SDK history"},
            (
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "double", "arguments": '{"value":7}'},
                        }
                    ]
                }
                if first
                else {"content": "14"}
            ),
        ]
        chunks = [
            {
                "id": "synthetic",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "deepseek-test",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            for delta in deltas
        ]
        chunks.append(
            {
                "id": "synthetic",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls" if first else "stop",
                    }
                ],
            }
        )
        chunks.append(
            {
                "id": "synthetic",
                "choices": [],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "prompt_cache_hit_tokens": 8,
                },
            }
        )
        sse = (
            "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    )
    handle = await create_provider_model(
        model_name="deepseek-test",
        base_url="https://deepseek.invalid/v1",
        provider="deepseek",
        api_key="synthetic-test-key",
        http_client=client,
        parameters={"thinking": {"type": "enabled"}},
        max_retries=0,
    )
    try:
        model = handle.model.bind_tools(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "double",
                        "description": "double a number",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    },
                }
            ]
        )
        sink = AsyncMock()
        observer = ExecutionObservations(sink)

        async def collect(messages):
            full = None
            async for chunk in model.astream(
                messages, config={"callbacks": [observer]}
            ):
                full = chunk if full is None else full + chunk
            return full

        first = await collect([HumanMessage(content="double 7")])
        assert (
            first.additional_kwargs["reasoning_content"]
            == "synthetic reasoning kept in SDK history"
        )
        assert first.tool_calls[0]["args"] == {"value": 7}
        await collect(
            [
                HumanMessage(content="double 7"),
                first,
                ToolMessage(content="14", tool_call_id="call_1"),
            ]
        )
        assert (
            requests[1]["messages"][1]["reasoning_content"]
            == first.additional_kwargs["reasoning_content"]
        )
        finished = [
            call.args[1] for call in sink.await_args_list if call.args[0] == "finish"
        ]
        assert len(finished) == 2 and all(
            row["cacheReadTokens"] == 8 for row in finished
        )
        assert (
            finished[0]["content"]["thinking"]["text"]
            == first.additional_kwargs["reasoning_content"]
        )
        assert finished[1]["content"]["output"]["text"] == "14"
    finally:
        await handle.aclose()
    assert client.is_closed
