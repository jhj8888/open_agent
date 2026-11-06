"""Bind official model callbacks to single-use, database-owned HTTP admission.

No request body, model credential or caller-declared token bound is sent to the ledger.
The normal execution permit remains independent so accounting cannot fence cleanup.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from typing import Any
from uuid import UUID

import httpx
from langchain_core.callbacks import AsyncCallbackHandler

from agent.durable.control import AttemptLease, ControlPlaneClient
from agent.engine.observations import ExecutionObservations


class ModelBudgetError(RuntimeError):
    def __init__(self):
        super().__init__("model_budget_unavailable")


@dataclass
class _Call:
    owner: ModelBudget
    identifier: str
    request_sha: str | None = None
    consumed: bool = False
    dispatched: bool = False
    counts: tuple[int, int] | None = None
    finalized: bool = False


_current: ContextVar[_Call | None] = ContextVar("model_budget_call", default=None)


class ModelBudget(AsyncCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self, control: ControlPlaneClient, lease: AttemptLease, model_name: str):
        self.control, self.lease, self.model_name = control, lease, model_name
        self.calls: dict[str, _Call] = {}
        self.closed = False

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        identifier = str(run_id)
        if self.closed or len(messages) != 1 or identifier in self.calls or len(self.calls) >= 1000:
            raise ModelBudgetError()
        try:
            if str(UUID(identifier)) != identifier:
                raise ValueError()
        except (ValueError, TypeError):
            raise ModelBudgetError() from None
        call = _Call(self, identifier)
        self.calls[identifier] = call
        _current.set(call)  # Official inline callbacks run in the invoking model's context.

    async def on_llm_start(self, serialized, prompts, **kwargs):
        # Only the approved Chat Completions adapters have a verified accounting contract.
        raise ModelBudgetError()

    async def authorize(self, request: httpx.Request) -> None:
        call = _current.get()
        if self.closed or call is None or call.owner is not self or call.consumed or call.finalized:
            raise ModelBudgetError()
        call.consumed = True  # Claim before any await; inherited contexts cannot send twice.
        try:
            content = request.content
            if request.method != "POST" or len(content) > 8 * 1024 * 1024:
                raise ValueError()
            payload = json.loads(content)
            if not isinstance(payload, dict) or payload.get("model") != self.model_name:
                raise ValueError()
            if type(payload.get("n", 1)) is not int or payload.get("n", 1) != 1:
                raise ValueError()
        except (ValueError, TypeError, httpx.RequestNotRead):
            raise ModelBudgetError() from None
        call.request_sha = hashlib.sha256(request.method.encode() + b"\0" + str(request.url).encode() + b"\0" + content).hexdigest()
        try:
            receipt = await self.control.model_budget(self.lease, "prepare", call.identifier, call.request_sha)
            if receipt.get("callId") != call.identifier or receipt.get("phase") != "prepared":
                raise ModelBudgetError()
            result = await self.control.model_budget(self.lease, "dispatch", call.identifier, call.request_sha)
            if result.get("permitted") is not True:
                raise ModelBudgetError()
            call.dispatched = True
        except BaseException as error:
            self.closed = True
            await self._abort(call)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise ModelBudgetError() from None

    async def on_llm_end(self, response, *, run_id, **kwargs):
        call = self.calls.get(str(run_id))
        if call is None or call.finalized:
            raise ModelBudgetError()
        if not call.consumed:
            call.finalized = True  # An official model cache hit performed no HTTP request.
            return
        if not call.dispatched:
            await self._abort(call)
            raise ModelBudgetError()
        counts = ExecutionObservations.usage(response)
        if counts is None:
            self.closed = True
            await self._abort(call)
            raise ModelBudgetError()  # Unknown consumes the bound; do not silently continue.
        call.counts = (counts["inputTokens"], counts["outputTokens"])
        await self._settle(call)

    async def on_llm_error(self, error, *, run_id, **kwargs):
        call = self.calls.get(str(run_id))
        if call is not None and not call.finalized:
            self.closed = True
            # Partial response usage cannot prove that billing has finished.
            if call.counts is None:
                await self._abort(call)
            else:
                await self._settle(call)

    async def _settle(self, call: _Call):
        try:
            result = await self.control.model_budget(self.lease, "settle", call.identifier, call.request_sha,
                                                     input_tokens=call.counts[0], output_tokens=call.counts[1])
            if result.get("callId") != call.identifier or result.get("phase") not in {"settled", "breached"}:
                raise ModelBudgetError()
            call.finalized = True
            if result["phase"] == "breached":
                self.closed = True
                raise ModelBudgetError()
        except asyncio.CancelledError:
            self.closed = True
            raise
        except Exception:
            self.closed = True
            raise ModelBudgetError() from None

    async def _abort(self, call: _Call):
        if call.finalized:
            return
        if call.request_sha is None:
            call.finalized = True
            return
        try:
            async with asyncio.timeout(3):
                cancelled = await self.control.model_budget(self.lease, "cancel", call.identifier, call.request_sha)
                if cancelled.get("cancelled") is True:
                    call.finalized = True
                    return
                result = await self.control.model_budget(self.lease, "unknown", call.identifier, call.request_sha)
                if result.get("callId") == call.identifier and result.get("phase") in {"unknown", "settled", "breached"}:
                    call.finalized = True
        except Exception:
            pass  # Persistent held/unknown is safer than inventing a successful release.

    async def close(self):
        self.closed = True
        for call in self.calls.values():
            if not call.finalized:
                if call.counts is not None:
                    await self._settle(call)  # Same ID and counts; never retries the model.
                else:
                    await self._abort(call)
