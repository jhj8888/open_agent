"""Official OTLP export from a durable control-plane outbox; never executes an Agent."""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import logging
from pathlib import Path
import time
from urllib.parse import urlsplit

import requests
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

from agent.durable.control import ControlPlaneClient, ControlPlaneError

MAX_PROJECTION_BYTES = 32 * 1024 * 1024
RESOURCE = Resource({"service.name": "open-agent-observation-exporter"})
SCOPE = InstrumentationScope("open_agent.persisted_observations", "1")
LOG = logging.getLogger(__name__)


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def nanos(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp requires timezone")
    delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (
        delta.days * 86400 + delta.seconds
    ) * 1_000_000_000 + delta.microseconds * 1000


def stable_id(parts: list, size: int) -> int:
    # JSON array prevents ambiguous concatenation. Never derive IDs from sensitive content.
    return (
        int.from_bytes(hashlib.sha256(encoded(parts).encode()).digest()[:size], "big")
        or 1
    )


def project(run: dict, rows: list[dict]) -> list[ReadableSpan]:
    """Hydrate official ReadableSpan objects without changing the global tracer provider."""
    if not rows or len(rows) > 4096:
        raise ValueError("Invalid projection count")
    identity = [run[k] for k in ("tenant_id", "environment", "run_id", "attempt_no")]
    trace = stable_id(["open_agent.trace.v1", *identity], 16)

    def context(step: str) -> SpanContext:
        return SpanContext(
            trace,
            stable_id(["open_agent.span.v1", *identity, step], 8),
            False,
            TraceFlags(1),
        )

    root_id = "platform-attempt-lifecycle"
    starts = {row["start_json"]["stepId"]: row["start_json"] for row in rows}
    finishes = {
        row["start_json"]["stepId"]: row["finish_json"]
        for row in rows
        if row["finish_json"] is not None
    }
    if len(starts) != len(rows) or root_id in starts:
        raise ValueError("Invalid projection identifiers")
    for step_id in starts:
        parent, visited = step_id, set()
        while parent in starts:
            if parent in visited:
                raise ValueError("Cyclic observation parents")
            visited.add(parent)
            parent = starts[parent].get("parentStepId")
    base = {
        "open_agent." + key: value
        for key, value in run.items()
        if key not in {"created_at", "released_at"}
    }
    spans: list[ReadableSpan] = []
    incomplete: list[Event] = []
    for step_id, start in starts.items():
        finish = finishes.get(step_id)
        if finish is None:
            incomplete.append(
                Event(
                    "open_agent.unfinished_step",
                    {
                        "open_agent.step_id": step_id,
                        "open_agent.kind": start["kind"],
                        "open_agent.name": start["name"],
                        "open_agent.parent_step_id": start.get("parentStepId") or "",
                        "open_agent.usage_source": (
                            "unknown"
                            if start["kind"] == "model"
                            else start["usageSource"]
                        ),
                    },
                    nanos(start["startedAt"]),
                )
            )
            continue
        parent = start.get("parentStepId")
        visited = {step_id}
        while parent and parent not in finishes:
            if parent in visited:
                raise ValueError("Cyclic observation parents")
            visited.add(parent)
            parent = starts.get(parent, {}).get("parentStepId")
        if parent == step_id:
            raise ValueError("Cyclic observation parents")
        attrs = {
            **base,
            "open_agent.step_id": step_id,
            "open_agent.kind": start["kind"],
            "open_agent.status": finish["status"],
            "open_agent.usage_source": finish["usageSource"],
            "open_agent.duration_ms": finish["durationMs"],
            "open_agent.original_parent_step_id": start.get("parentStepId") or "",
            "open_agent.start_summary": encoded(start.get("summary", {})),
            "open_agent.finish_summary": encoded(finish.get("summary", {})),
        }
        kind = start["kind"]
        if kind in {"model", "tool", "agent"}:
            attrs["gen_ai.operation.name"] = {
                "model": "chat",
                "tool": "execute_tool",
                "agent": "invoke_agent",
            }[kind]
        if kind == "tool":
            attrs["gen_ai.tool.name"] = start["name"]
        if kind == "model" and finish["usageSource"] == "provider":
            # Only actual model leaves. Cache-read tokens are already included in input_tokens.
            attrs["gen_ai.usage.input_tokens"] = finish["inputTokens"]
            attrs["gen_ai.usage.output_tokens"] = finish["outputTokens"]
            for field in ("cacheReadTokens", "cacheWriteTokens"):
                if finish.get(field) is not None:
                    attrs["open_agent." + field] = finish[field]
        for phase, key in (
            (start, "mlflow.spanInputs"),
            (finish, "mlflow.spanOutputs"),
        ):
            if phase.get("content") is not None:
                # Preserve availability, truncation and incremental context markers, including actual think.
                attrs[key] = encoded(phase["content"])
        beginning, end = nanos(start["startedAt"]), nanos(finish["endedAt"])
        if end < beginning:
            raise ValueError("Invalid observation time range")
        spans.append(
            ReadableSpan(
                start["name"],
                context=context(step_id),
                parent=context(parent or root_id),
                resource=RESOURCE,
                attributes=attrs,
                start_time=beginning,
                end_time=end,
                instrumentation_scope=SCOPE,
                kind=SpanKind.CLIENT if kind == "model" else SpanKind.INTERNAL,
                status=Status(
                    StatusCode.OK
                    if finish["status"] == "succeeded"
                    else StatusCode.ERROR
                ),
            )
        )
    # The root represents authoritative lease lifecycle, never an invented end for unfinished callbacks.
    # It is exported last so a partially delivered trace is not prematurely marked complete.
    beginning, end = nanos(run["created_at"]), nanos(run["released_at"])
    if end < beginning:
        raise ValueError("Invalid lifecycle time range")
    spans.append(
        ReadableSpan(
            "agent.attempt.lifecycle",
            context=context(root_id),
            resource=RESOURCE,
            attributes={
                **base,
                "open_agent.lifecycle_source": "execution_lease",
                "open_agent.unfinished_steps": len(incomplete),
            },
            events=incomplete,
            start_time=beginning,
            end_time=end,
            instrumentation_scope=SCOPE,
            status=Status(
                StatusCode.OK if run["state"] == "succeeded" else StatusCode.ERROR
            ),
        )
    )
    return spans


class PrivateSession(requests.Session):
    """Official exporter session extension: never follow a redirect carrying private trace data."""

    def __init__(self):
        super().__init__()
        self.trust_env = False

    def post(self, url, *args, **kwargs):
        kwargs["allow_redirects"] = False
        return super().post(url, *args, **kwargs)


def official_exporter(config: dict) -> OTLPSpanExporter:
    endpoint = config["endpoint"]
    parsed = urlsplit(endpoint)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or (parsed.scheme == "http" and not loopback)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1/traces"
        or any(ord(c) <= 32 or c in "\\%" for c in endpoint)
    ):
        raise ValueError("Invalid private observation endpoint")
    auth = base64.b64encode(
        (config["username"] + ":" + config["password"]).encode()
    ).decode()
    return OTLPSpanExporter(
        endpoint=endpoint,
        headers={
            "Authorization": "Basic " + auth,
            "x-mlflow-experiment-id": str(config["experimentId"]),
        },
        timeout=8,
        session=PrivateSession(),
        compression=Compression.Gzip,
        certificate_file=config.get("caFile"),
        max_request_size=8 * 1024 * 1024,
    )


class DurableExporter:
    def __init__(self, control: ControlPlaneClient, exporter: OTLPSpanExporter):
        self.control, self.exporter = control, exporter

    async def once(self) -> bool:
        claims = await self.control.request(
            "/internal/platform/trace-export/claim", {"leaseSeconds": 300}
        )
        if not claims:
            return False
        claim = claims[0]
        path = f"/internal/platform/trace-export/{int(claim['id'])}/"
        body = {"claimId": claim["claimId"], "revision": claim["revision"]}
        failure = "control_unavailable"
        try:
            async with asyncio.timeout(240):
                rows, after, size, metadata = [], 0, 0, None
                while True:
                    page = await self.control.request(
                        path + "page", {**body, "after": after}, limit=8 * 1024 * 1024
                    )
                    size += len(encoded(page).encode())
                    if (
                        size > MAX_PROJECTION_BYTES
                        or len(rows) + len(page["items"]) > 4096
                    ):
                        failure = "payload_limit"
                        raise ValueError("Projection limit")
                    if metadata is not None and metadata != page["run"]:
                        raise ValueError("Projection changed")
                    metadata = page["run"]
                    rows.extend(page["items"])
                    if not page["hasMore"]:
                        break
                    if page["next"] <= after:
                        raise ValueError("Invalid cursor")
                    after = page["next"]
                failure = "invalid_projection"
                spans = project(metadata, rows)
                failure = "backend_unavailable"
                if not await asyncio.to_thread(self.send, spans):
                    raise RuntimeError("Export failed")
                failure = "control_unavailable"
                ack = await self.control.request(path + "ack", body)
                if ack.get("accepted") is not True:
                    raise RuntimeError("Claim lost")
                LOG.info(
                    "observation_export_delivered id=%s revision=%s spans=%s",
                    claim["id"],
                    claim["revision"],
                    len(spans),
                )
                return True
        except (
            ControlPlaneError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            TimeoutError,
        ):
            # Stable error classes only; no SDK exception, body or credentials in logs.
            LOG.warning(
                "observation_export_deferred id=%s category=%s", claim["id"], failure
            )
            delay = min(300, 5 * (2 ** min(int(claim["deliveryAttempt"]) - 1, 6)))
            try:
                await self.control.request(
                    path + "retry", {**body, "delaySeconds": delay, "error": failure}
                )
            except ControlPlaneError:
                pass  # Claim expiry recovers even if the control plane is unavailable.
            return True

    def send(self, spans: list[ReadableSpan]) -> bool:
        deadline = time.monotonic() + 210
        for offset in range(0, len(spans), 32):
            if time.monotonic() > deadline:
                return False
            if (
                self.exporter.export(spans[offset : offset + 32])
                != SpanExportResult.SUCCESS
            ):
                return False
        return True


async def serve(config: dict, once: bool) -> None:
    control = ControlPlaneClient(
        config["controlEndpoint"],
        config["controlKeyId"],
        config["controlKey"],
        ca_file=config.get("controlCaFile"),
    )
    exporter = official_exporter(config["backend"])
    service = DurableExporter(control, exporter)
    try:
        while True:
            try:
                busy = await service.once()
            except ControlPlaneError:
                LOG.warning("observation_export_control_unavailable")
                busy = False
            if once:
                return
            await asyncio.sleep(0.5 if busy else 5)
    finally:
        exporter.shutdown()
        await control.http.aclose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    path = Path(args.config)
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("Invalid observation configuration")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # The official transport may log response bodies on rejection; keep only our bounded event codes.
    for name in (
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.exporter.http_transport",
    ):
        logging.getLogger(name).disabled = True
    asyncio.run(serve(json.loads(path.read_text(encoding="utf-8")), args.once))


if __name__ == "__main__":
    main()
