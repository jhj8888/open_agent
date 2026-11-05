"""Official inherited LangChain callbacks, with bounded metadata-only platform projection.

Never serialize model prompts, tool argument values, results, exception messages or
framework metadata. Callback UUIDs identify actual calls, not delivery attempts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import re
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult


def label(value: Any, fallback: str = "step") -> str:
    # Only bounded identifiers; never str() arbitrary framework objects.
    return (
        (re.sub(r"[^A-Za-z0-9_./: -]", "_", value[:128]) or fallback)
        if isinstance(value, str)
        else fallback
    )


def shape(value: Any) -> str:
    if isinstance(value, dict):
        fields = [
            f"{label(key)}:{type(item).__name__}"
            for key, item in list(value.items())[:20]
        ]
        return "fields=" + ", ".join(fields)
    if isinstance(value, (str, list, tuple, bytes)):
        return f"{type(value).__name__}; length={len(value)}"
    return type(value).__name__[:64]


def timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class ExecutionObservations(AsyncCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self, publish: Callable[[str, dict], Awaitable[None]]):
        self.publish = publish
        self.open: dict[str, tuple[dict, float]] = {}
        self.parents: dict[str, str] = {}
        self.tool_steps: dict[str, str] = {}

    async def start(
        self,
        key: str,
        kind: str,
        name: str,
        parent: str | None = "attempt",
        summary: dict | None = None,
    ):
        key = str(key)
        if key in self.open:
            return
        self.parents[key] = parent
        began = time.monotonic()
        row = {
            "stepId": key,
            "parentStepId": parent,
            "kind": kind,
            "name": label(name),
            "startedAt": timestamp(),
            "endedAt": None,
            "durationMs": None,
            "status": "running",
            "usageSource": (
                "unknown"
                if kind == "model"
                else "aggregate" if kind in {"agent", "chain"} else "not_applicable"
            ),
            "inputTokens": None,
            "outputTokens": None,
            "cacheReadTokens": None,
            "cacheWriteTokens": None,
            "summary": summary or {},
        }
        await self.publish("start", row)
        self.open[key] = (row, began)
        return key

    async def finish(self, key, status="succeeded", *, summary=None, counts=None):
        key = str(key)
        if key not in self.open:
            return
        start, began = self.open[key]
        row = {
            **start,
            "summary": summary or {},
            "status": status,
            "endedAt": max(start["startedAt"], timestamp()),
            "durationMs": max(0, int((time.monotonic() - began) * 1000)),
        }
        if start["kind"] == "model" and counts:
            row.update(counts)
        await self.publish("finish", row)
        self.open.pop(key, None)

    async def interrupt_open(self):
        for key in reversed(list(self.open)):
            await self.finish(
                key, "interrupted", summary={"errorType": "ExecutionInterrupted"}
            )

    async def on_chain_start(
        self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs
    ):
        name = kwargs.get("name") or (serialized or {}).get("name") or "chain"
        await self.start(
            str(run_id),
            "chain",
            name,
            str(parent_run_id) if parent_run_id else "attempt",
            {"input": shape(inputs)},
        )

    async def on_chain_end(self, outputs, *, run_id, **kwargs):
        await self.finish(run_id, summary={"output": shape(outputs)})

    async def on_chain_error(self, error, *, run_id, **kwargs):
        await self.finish(
            run_id,
            "interrupted" if isinstance(error, asyncio.CancelledError) else "failed",
            summary={"errorType": label(type(error).__name__)},
        )

    async def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, **kwargs
    ):
        # Model name is not pulled from credentials/serialized kwargs.
        name = (kwargs.get("metadata") or {}).get("ls_model_name") or "chat_model"
        description = "; ".join(
            f"{label(message.type)}:{len(message.content)}"
            for batch in messages[:1]
            for message in batch[:80]
        )
        await self.start(
            str(run_id),
            "model",
            name,
            str(parent_run_id) if parent_run_id else "attempt",
            {"input": description[:2048]},
        )

    async def on_llm_start(
        self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs
    ):
        await self.start(
            str(run_id),
            "model",
            "completion_model",
            str(parent_run_id) if parent_run_id else "attempt",
            {"input": shape(prompts)},
        )

    @staticmethod
    def usage(response: LLMResult) -> dict | None:
        # One callback corresponds to one logical model request. Alternative candidates
        # are not separate calls. DeepAgents uses one input and one chat generation.
        try:
            generation = response.generations[0][0]
        except (IndexError, AttributeError):
            return None
        if not isinstance(generation, ChatGeneration) or not isinstance(
            generation.message, AIMessage
        ):
            return None
        counts = generation.message.usage_metadata
        if not counts or any(
            type(counts.get(key)) is not int or not 0 <= counts[key] <= 1000000000
            for key in ("input_tokens", "output_tokens")
        ):
            return None
        details = counts.get("input_token_details") or {}

        def optional(key):
            value = details.get(key)
            return (
                value
                if type(value) is int and 0 <= value <= counts["input_tokens"]
                else None
            )

        return {
            "usageSource": "provider",
            "inputTokens": counts["input_tokens"],
            "outputTokens": counts["output_tokens"],
            "cacheReadTokens": optional("cache_read"),
            "cacheWriteTokens": optional("cache_creation"),
        }

    async def on_llm_end(self, response, *, run_id, **kwargs):
        await self.finish(
            run_id,
            counts=self.usage(response),
            summary={"output": "model response received"},
        )

    async def on_llm_error(self, error, *, run_id, **kwargs):
        # Partial streaming usage, if actually provided by the framework, remains provider data.
        counts = self.usage(kwargs.get("response"))
        await self.finish(
            run_id,
            "interrupted" if isinstance(error, asyncio.CancelledError) else "failed",
            counts=counts,
            summary={"errorType": label(type(error).__name__)},
        )

    async def on_tool_start(
        self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs
    ):
        summary = {"input": shape(kwargs.get("inputs"))}
        call_id = kwargs.get("tool_call_id")
        if isinstance(call_id, str) and re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,120}", call_id
        ):
            summary["toolCallId"] = call_id
            self.tool_steps["tool:" + call_id] = str(run_id)
        await self.start(
            str(run_id),
            "tool",
            (serialized or {}).get("name", "tool"),
            str(parent_run_id) if parent_run_id else "attempt",
            summary,
        )

    async def on_tool_end(self, output, *, run_id, **kwargs):
        # ToolMessage.status='error' represents handled tool failures that don't throw.
        failed = getattr(output, "status", None) == "error"
        await self.finish(
            run_id,
            "failed" if failed else "succeeded",
            summary={"output": shape(getattr(output, "content", output))},
        )

    async def on_tool_error(self, error, *, run_id, **kwargs):
        await self.on_chain_error(error, run_id=run_id)

    async def custom(self, kind, name, parent="attempt", summary=None):
        return await self.start(str(uuid4()), kind, name, parent, summary)
