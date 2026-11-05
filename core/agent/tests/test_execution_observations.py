"""Per-call identity, provider usage, metadata boundary and terminal lifecycle contracts."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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
    assert leaf[0]["content"]["output"]["text"] == "private response"
    assert leaf[0]["content"]["thinking"]["availability"] == "not_returned"
    assert all("content" not in row for row in finished if row["kind"] == "chain")


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
    assert finished[1]["content"]["toolResult"]["text"] == "secret error"
    assert "private cancellation" not in str(sink.await_args_list)


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


@pytest.mark.asyncio
async def test_delta_inputs_reasoning_and_tool_results_are_redacted_without_mutating_history():
    from agent.engine.observation_content import ObservationContent

    sink = AsyncMock()
    observer = ExecutionObservations(sink, secrets=("synthetic-test-key",))
    user = HumanMessage(content="请计算，并记录 password=do-not-store")
    first, tool, second, compressed = [uuid4() for _ in range(4)]
    await observer.on_chat_model_start({}, [[user]], run_id=first)
    answer = AIMessage(
        content="",
        additional_kwargs={"reasoning_content": "先使用工具计算 synthetic-test-key"},
        tool_calls=[
            {
                "id": "call_a",
                "name": "calculate",
                "args": {"value": 7, "api_key": "do-not-store"},
            }
        ],
    )
    await observer.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=answer)]]), run_id=first
    )
    await observer.on_tool_start(
        {"name": "calculate"},
        "",
        run_id=tool,
        inputs={"value": 7, "password": "do-not-store", "runtime": object()},
    )
    result = ToolMessage(
        content="14; Bearer abcdefghijkl; sk-synthetic123456", tool_call_id="call_a"
    )
    await observer.on_tool_end(result, run_id=tool)
    await observer.on_chat_model_start({}, [[user, answer, result]], run_id=second)
    await observer.on_chat_model_start(
        {}, [[HumanMessage(content="compressed context")]], run_id=compressed
    )
    records = [call.args[1] for call in sink.await_args_list]
    assert records[0]["content"]["inputMode"] == "initial"
    assert records[4]["content"]["inputMode"] == "append"
    assert records[4]["content"]["previousStepId"] == str(first)
    assert "请计算" not in records[4]["content"]["input"]["text"]
    assert records[5]["content"]["inputMode"] == "context_changed"
    assert records[1]["content"]["thinking"]["text"] == "先使用工具计算 [REDACTED]"
    for secret in (
        "do-not-store",
        "synthetic-test-key",
        "abcdefghijk",
        "sk-synthetic123456",
    ):
        assert secret not in str(records)
    assert "synthetic-test-key" in answer.additional_kwargs["reasoning_content"]
    assert "do-not-store" in user.content
    assert (
        observer.content.block("bare echo do-not-store", "model_message")["text"]
        == "bare echo [REDACTED]"
    )
    independent = ObservationContent()
    assert (
        independent.block("bare echo do-not-store", "model_message")["text"]
        == "bare echo do-not-store"
    )
    limited = ObservationContent().block("开头" + "界" * 9000 + "末尾", "tool_message")
    assert limited["truncated"] and len(limited["text"].encode()) <= 6144
    assert limited["text"].startswith("开头") and limited["text"].endswith("末尾")


@pytest.mark.asyncio
async def test_partial_stream_retains_only_actually_returned_content():
    sink = AsyncMock()
    observer = ExecutionObservations(sink)
    key = uuid4()
    await observer.on_chat_model_start({}, [[]], run_id=key)
    partial = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="partial answer",
                        additional_kwargs={"reasoning_content": "partial reasoning"},
                    )
                )
            ]
        ]
    )
    await observer.on_llm_error(asyncio.CancelledError(), run_id=key, response=partial)
    end = sink.await_args.args[1]
    assert end["status"] == "interrupted" and end["inputTokens"] is None
    assert end["content"]["thinking"]["availability"] == "partial"
    assert end["content"]["output"]["text"] == "partial answer"
