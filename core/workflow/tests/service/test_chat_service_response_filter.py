import asyncio
import json
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from workflow.consts.app_audit import AppAuditPolicy
from workflow.consts.engine.chat_status import ChatStatus
from workflow.consts.engine.timeout import QueueTimeout
from workflow.engine.callbacks.openai_types_sse import (
    Choice,
    Delta,
    LLMGenerate,
    NodeInfo,
    WorkflowStep,
)
from workflow.service import chat_service
from workflow.service.chat_service import _filter_response_frame, _get_response


def test_release_filter_keeps_end_node_variable_content() -> None:
    response = LLMGenerate(
        id="sid",
        workflow_step=WorkflowStep(
            node=NodeInfo(
                id="node-end::1",
                finish_reason=ChatStatus.FINISH_REASON.value,
                ext={"answer_mode": 0},
            ),
            progress=1,
        ),
        choices=[Choice(delta=Delta(content='{"output":[{"name":"test"}]}'), index=0)],
    )
    last_workflow_step = WorkflowStep(seq=3)

    filtered = _filter_response_frame(
        response_frame=response,
        is_stream=True,
        last_workflow_step=last_workflow_step,
        message_cache=[],
        reasoning_content_cache=[],
        is_release=True,
    )

    assert filtered is not None
    assert filtered.workflow_step.node is None
    assert filtered.choices[0].delta.content == '{"output":[{"name":"test"}]}'


def test_release_filter_keeps_workflow_end_content() -> None:
    response = LLMGenerate(
        id="sid",
        workflow_step=WorkflowStep(node=NodeInfo(id="flow_obj"), progress=1),
        choices=[
            Choice(
                delta=Delta(content='{"output":[{"name":"test"}]}'),
                index=0,
                finish_reason=ChatStatus.FINISH_REASON.value,
            )
        ],
    )
    last_workflow_step = WorkflowStep(seq=3)

    filtered = _filter_response_frame(
        response_frame=response,
        is_stream=True,
        last_workflow_step=last_workflow_step,
        message_cache=[],
        reasoning_content_cache=[],
        is_release=True,
    )

    assert filtered is not None
    assert filtered.workflow_step.node is None
    assert filtered.workflow_step.seq == 4
    assert filtered.choices[0].delta.content == '{"output":[{"name":"test"}]}'


@pytest.mark.asyncio
async def test_idle_workflow_sends_heartbeat_before_30_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeouts: list[float] = []

    async def timeout_immediately(awaitable: Any, *, timeout: float) -> None:
        observed_timeouts.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timeout_immediately)

    response = await _get_response(
        app_audit_policy=AppAuditPolicy.DEFAULT,
        audit_strategy=None,
        response_queue=asyncio.Queue(),
        last_response=None,
    )

    assert observed_timeouts == [15]
    assert QueueTimeout.PingQT.value == 15
    assert response.choices[0].finish_reason == "ping"


@pytest.mark.asyncio
async def test_normal_chat_trace_recursively_redacts_legacy_sandbox_api_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camel_case_secret = "CAMEL-CASE-E2B-KEY-MUST-NOT-REACH-TRACE"
    snake_case_secret = "SNAKE-CASE-E2B-KEY-MUST-NOT-REACH-TRACE"
    workflow_dsl = {
        "nodes": [
            {
                "data": {
                    "nodeParam": {
                        "sandbox": {
                            "apiKey": camel_case_secret,
                            "nested": {"api_key": snake_case_secret},
                            "workflowId": "flow-1",
                        }
                    }
                }
            }
        ]
    }
    recorded_events: list[str] = []

    class FakeMeter:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def set_label(self, *args: Any, **kwargs: Any) -> None:
            return None

        def in_error_count(self, *args: Any, **kwargs: Any) -> None:
            return None

    class FakeSpanContext:
        def record_exception(self, error: Exception) -> None:
            return None

    class FakeSpan:
        sid = "trace-sid"

        def start(self, *args: Any, **kwargs: Any) -> Any:
            return nullcontext(FakeSpanContext())

        async def add_info_event_async(self, event: str) -> None:
            recorded_events.append(event)

    async def stop_after_trace(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("stop after trace capture")

    monkeypatch.setattr(chat_service, "Meter", FakeMeter)
    monkeypatch.setattr(
        chat_service, "langfuse_trace_context", lambda *args, **kwargs: nullcontext()
    )
    monkeypatch.setattr(chat_service, "_init_workflow_trace", lambda *args: object())
    monkeypatch.setattr(chat_service, "_get_or_build_workflow_engine", stop_after_trace)
    monkeypatch.setattr(chat_service, "kafka_report", lambda **kwargs: None)
    chat_vo = SimpleNamespace(
        flow_id="flow-1",
        uid="user-1",
        chat_id="chat-1",
        version="v1",
        ext={},
        parameters={},
        json=lambda: "{}",
    )

    await chat_service._run(
        app_alias_id="app-1",
        event_id="event-1",
        workflow_dsl=workflow_dsl,
        workflow_dsl_update_time=datetime.now(),
        chat_vo=chat_vo,
        is_release=False,
        app_audit_policy=AppAuditPolicy.DEFAULT,
        response_queue=asyncio.Queue(),
        span=FakeSpan(),
    )

    trace_event = next(
        event for event in recorded_events if event.startswith("spark dsl: ")
    )
    assert camel_case_secret not in trace_event
    assert snake_case_secret not in trace_event
    sanitized = json.loads(trace_event.removeprefix("spark dsl: "))
    sandbox = sanitized["nodes"][0]["data"]["nodeParam"]["sandbox"]
    assert sandbox == {"nested": {}, "workflowId": "flow-1"}
