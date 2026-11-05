"""Per-call identity, provider usage, metadata boundary and terminal lifecycle contracts."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult
import pytest

from agent.engine.observations import ExecutionObservations


def response(usage=None):
    return LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(content="private response", usage_metadata=usage)
                )
            ]
        ]
    )


@pytest.mark.asyncio
async def test_each_leaf_call_has_provider_usage_and_parent_counts_are_not_repeated():
    sink = AsyncMock()
    observer = ExecutionObservations(sink)
    await observer.start("attempt", "agent", "DeepAgents", None)
    chain, first, compressed, retry = [uuid4() for _ in range(4)]
    await observer.on_chain_start(
        None, {"messages": "private"}, run_id=chain, name="SummarizationMiddleware"
    )
    for call in (first, compressed, retry):
        await observer.on_chat_model_start(
            {}, [[AIMessage(content="private input")]], run_id=call, parent_run_id=chain
        )
        await observer.on_llm_end(
            response(
                {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "input_token_details": {"cache_read": 80, "cache_creation": 5},
                }
            ),
            run_id=call,
        )
    await observer.on_chain_end({}, run_id=chain)
    await observer.finish("attempt")
    finished = [
        call.args[1] for call in sink.await_args_list if call.args[0] == "finish"
    ]
    leaf = [row for row in finished if row["kind"] == "model"]
    assert len({row["stepId"] for row in leaf}) == 3
    assert sum(row["inputTokens"] + row["outputTokens"] for row in leaf) == 360
    assert all(
        row["cacheReadTokens"] == 80 and row["parentStepId"] == str(chain)
        for row in leaf
    )
    assert all(row["inputTokens"] is None for row in finished if row["kind"] != "model")
    assert "private" not in str(sink.await_args_list)


@pytest.mark.asyncio
async def test_unknown_tool_errors_and_interrupted_calls_are_not_reported_as_zero_usage():
    sink = AsyncMock()
    observer = ExecutionObservations(sink)
    unknown, tool, pending = [uuid4() for _ in range(3)]
    await observer.on_chat_model_start({}, [], run_id=unknown)
    await observer.on_llm_end(response(), run_id=unknown)
    await observer.on_tool_start(
        {"name": "execute"},
        "secret command",
        run_id=tool,
        inputs={"command": "secret command"},
        tool_call_id="call_1",
    )
    await observer.on_tool_end(
        ToolMessage(content="secret error", tool_call_id="call_1", status="error"),
        run_id=tool,
    )
    await observer.on_chat_model_start({}, [], run_id=pending)
    await observer.on_llm_error(
        asyncio.CancelledError("private cancellation"), run_id=pending
    )
    finished = [
        call.args[1] for call in sink.await_args_list if call.args[0] == "finish"
    ]
    assert [row["status"] for row in finished] == ["succeeded", "failed", "interrupted"]
    assert [row["usageSource"] for row in finished] == [
        "unknown",
        "not_applicable",
        "unknown",
    ]
    assert all(row["inputTokens"] is None for row in finished)
    assert observer.tool_steps["tool:call_1"] == str(tool)
    assert "secret" not in str(sink.await_args_list) and "private" not in str(
        sink.await_args_list
    )


@pytest.mark.asyncio
async def test_failed_start_prevents_call_and_unfinished_steps_are_interrupted():
    sink = AsyncMock()
    observer = ExecutionObservations(sink)
    await observer.start("attempt", "agent", "DeepAgents", None)
    sink.side_effect = RuntimeError("control unavailable")
    with pytest.raises(RuntimeError):
        await observer.start("never-started", "model", "chat_model")
    assert "never-started" not in observer.open
    sink.side_effect = None
    await observer.interrupt_open()
    assert not observer.open
