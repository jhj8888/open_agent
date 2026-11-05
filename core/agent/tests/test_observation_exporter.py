import copy
import json
from unittest.mock import Mock

import pytest
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace.export import SpanExportResult

from agent.observability.exporter import (
    DurableExporter,
    PrivateSession,
    official_exporter,
    project,
)


def fixture():
    run = dict(
        tenant_id="tenant-a",
        environment="test",
        run_id="d75e3d38-43dc-461c-bdee-41463438b50a",
        deployment_id="demo",
        state="succeeded",
        attempt_no=1,
        created_at="2026-11-05T01:00:00Z",
        released_at="2026-11-05T01:00:05Z",
    )

    def row(identifier, parent=None, done=True, kind="model"):
        start = dict(
            stepId=identifier,
            parentStepId=parent,
            name=identifier,
            kind=kind,
            startedAt="2026-11-05T01:00:01.123Z",
            usageSource="unknown",
            summary={},
            content={"inputMode": "append", "input": {"text": "incremental input"}},
        )
        finish = (
            {
                **start,
                "endedAt": "2026-11-05T01:00:02.234Z",
                "durationMs": 1111,
                "usageSource": "provider" if kind == "model" else "not_applicable",
                "status": "succeeded",
                "inputTokens": 10,
                "outputTokens": 2,
                "cacheReadTokens": 4,
                "content": {
                    "thinking": {"text": "actual provider reasoning"},
                    "output": {"text": "answer"},
                },
            }
            if done
            else None
        )
        return {"id": len(identifier), "start_json": start, "finish_json": finish}

    return run, [
        row("unfinished", done=False, kind="chain"),
        row("model", "unfinished"),
        row("tool", "model", kind="tool"),
    ]


def test_official_projection_is_stable_scoped_and_does_not_fabricate_unfinished_spans():
    run, rows = fixture()
    spans = project(run, rows)
    assert [s.name for s in spans] == ["model", "tool", "agent.attempt.lifecycle"]
    assert spans[0].parent.span_id == spans[-1].context.span_id
    assert spans[1].parent.span_id == spans[0].context.span_id
    assert spans[0].attributes["open_agent.original_parent_step_id"] == "unfinished"
    assert spans[-1].events[0].timestamp == spans[0].start_time
    assert spans[-1].attributes["open_agent.unfinished_steps"] == 1
    assert "gen_ai.usage.input_tokens" not in spans[-1].attributes
    assert "gen_ai.usage.input_tokens" not in spans[1].attributes
    assert spans[0].attributes["gen_ai.usage.input_tokens"] == 10
    assert spans[0].attributes["open_agent.cacheReadTokens"] == 4
    assert (
        json.loads(spans[0].attributes["mlflow.spanOutputs"])["thinking"]["text"]
        == "actual provider reasoning"
    )
    wire = encode_spans(spans).SerializeToString()
    assert wire == encode_spans(project(run, rows)).SerializeToString()
    for field, value in (
        ("tenant_id", "tenant-b"),
        ("environment", "prod"),
        ("attempt_no", 2),
    ):
        assert (
            project({**run, field: value}, rows)[0].context.trace_id
            != spans[0].context.trace_id
        )


def test_cycles_and_invalid_timestamp_rejected():
    run, rows = fixture()
    rows[1]["start_json"]["parentStepId"] = "tool"
    with pytest.raises(ValueError, match="Cyclic"):
        project(run, rows)
    run, rows = fixture()
    rows[1]["finish_json"]["endedAt"] = "2026-11-05T00:00:00Z"
    with pytest.raises(ValueError, match="time range"):
        project(run, rows)


class Control:
    def __init__(self):
        self.calls = []

    async def request(self, path, body, **kwargs):
        self.calls.append((path, copy.deepcopy(body)))
        if path.endswith("/claim"):
            return [
                {"id": 7, "claimId": "fixed-claim", "revision": 3, "deliveryAttempt": 2}
            ]
        if path.endswith("/page"):
            run, rows = fixture()
            return {"run": run, "items": rows, "hasMore": False, "next": 3}
        return {"accepted": True}


@pytest.mark.asyncio
async def test_backend_failure_only_retries_outbox_and_recovery_reuses_span_ids():
    control = Control()
    exporter = Mock()
    batches = []

    def export(spans):
        batches.append(encode_spans(spans).SerializeToString())
        return (
            SpanExportResult.FAILURE if len(batches) == 1 else SpanExportResult.SUCCESS
        )

    exporter.export.side_effect = export
    service = DurableExporter(control, exporter)
    assert await service.once()
    assert control.calls[-1][0].endswith("/retry")
    assert control.calls[-1][1]["delaySeconds"] == 10
    assert all(not path.endswith("/ack") for path, _ in control.calls)
    assert await service.once()
    assert control.calls[-1][0].endswith("/ack")
    assert batches[0] == batches[1]
    assert all(
        path.startswith("/internal/platform/trace-export/") for path, _ in control.calls
    )


@pytest.mark.asyncio
async def test_lost_ack_relinquishes_without_marking_success():
    class Stale(Control):
        async def request(self, path, body, **kwargs):
            result = await super().request(path, body, **kwargs)
            return {"accepted": False} if path.endswith("/ack") else result

    control = Stale()
    exporter = Mock()
    exporter.export.return_value = SpanExportResult.SUCCESS
    await DurableExporter(control, exporter).once()
    assert control.calls[-1][0].endswith("/retry")
    assert control.calls[-1][1]["error"] == "control_unavailable"


def test_private_session_disables_proxy_and_redirects(monkeypatch):
    sent = Mock()
    monkeypatch.setattr("requests.Session.post", sent)
    session = PrivateSession()
    session.post("http://127.0.0.1:18750/v1/traces", data=b"fixture")
    assert session.trust_env is False
    assert sent.call_args.kwargs["allow_redirects"] is False
    with pytest.raises(ValueError):
        official_exporter({"endpoint": "http://public.example/v1/traces"})
