import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from agent.durable.control import AttemptLease, ControlPlaneClient, ControlPlaneError
from agent.durable.contracts import RunRef
from agent.durable.model_budget import ModelBudget, ModelBudgetError
from agent.durable.permit import ExecutionPermit, PermitTransport


class Ledger:
    """Explicit protocol fixture; MySQL atomicity is tested separately in Java."""
    def __init__(self):
        self.rows = {}
        self.events = []
        self.drop_dispatch_response = False
        self.drop_settlement_response = False
        self.reject_prepare = False
        self.permit = AsyncMock(return_value=True)

    async def model_budget(self, lease, operation, identifier, sha, **usage):
        self.events.append((operation, identifier, sha, usage))
        if operation == "prepare":
            if self.reject_prepare:
                raise ControlPlaneError("CONTROL_HTTP_429")
            self.rows.setdefault(identifier, {"callId": identifier, "phase": "prepared", "sha": sha})
        row = self.rows.get(identifier)
        if row is None:
            raise ControlPlaneError("CONTROL_HTTP_404")
        assert row["sha"] == sha
        if operation == "dispatch":
            granted = row["phase"] == "prepared"
            if granted:
                row["phase"] = "dispatched"
            if self.drop_dispatch_response:
                raise ControlPlaneError("CONTROL_UNAVAILABLE")
            return {"permitted": granted}
        if operation == "cancel":
            cancelled = row["phase"] in {"prepared", "cancelled"}
            if cancelled:
                row["phase"] = "cancelled"
            return {"cancelled": cancelled}
        if operation == "unknown":
            if row["phase"] == "dispatched":
                row["phase"] = "unknown"
        if operation == "settle":
            if "counts" in row:
                assert row["counts"] == usage
            row["counts"] = usage
            row["phase"] = "settled"
            if self.drop_settlement_response:
                self.drop_settlement_response = False
                raise ControlPlaneError("CONTROL_UNAVAILABLE")
        return dict(row)


def setup():
    lease = AttemptLease(RunRef("tenant-a", "test", str(uuid4())), 1, str(uuid4()), 1)
    control = Ledger()
    budget = ModelBudget(control, lease, "fixture")
    permit = ExecutionPermit(control, lease, datetime.now(timezone.utc) + timedelta(minutes=1))
    return control, budget, permit


def result(input_tokens=20, output_tokens=5):
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content="fixture", usage_metadata={
        "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens+output_tokens,
        "input_token_details": {"cache_read": 10 if input_tokens >= 10 else 0},
    }))]])


@pytest.mark.asyncio
async def test_parallel_contexts_and_inherited_context_replay_are_isolated():
    control, budget, permit = setup()
    sent = []
    async def respond(request):
        sent.append(request.content)
        return httpx.Response(200, json={"ok": True})
    async with httpx.AsyncClient(transport=PermitTransport(permit, httpx.MockTransport(respond), model_budget=budget)) as client:
        async def run(index):
            identifier = uuid4()
            await budget.on_chat_model_start({}, [[]], run_id=identifier)
            await asyncio.sleep(0)
            await client.post("https://fixture.invalid/v1/chat/completions", json={"model":"fixture","messages":[str(index)]})
            with pytest.raises(ModelBudgetError):
                await asyncio.create_task(client.post("https://fixture.invalid/v1/chat/completions", json={"model":"fixture"}))
            await budget.on_llm_end(result(), run_id=identifier)
        await asyncio.gather(*(run(index) for index in range(8)))
    assert len(sent) == len(control.rows) == 8
    assert all(row["phase"] == "settled" and row["counts"] == {"input_tokens":20,"output_tokens":5} for row in control.rows.values())
    assert len({event[2] for event in control.events}) == 8
    await budget.close()


@pytest.mark.asyncio
async def test_lost_dispatch_response_never_sends_model_or_releases_dispatched_budget():
    control, budget, permit = setup()
    control.drop_dispatch_response = True
    sent = []
    async def respond(request):
        sent.append(request)
        return httpx.Response(200)
    identifier = uuid4()
    await budget.on_chat_model_start({}, [[]], run_id=identifier)
    async with httpx.AsyncClient(transport=PermitTransport(permit, httpx.MockTransport(respond), model_budget=budget)) as client:
        for _ in range(2):
            with pytest.raises(ModelBudgetError):
                await client.post("https://fixture.invalid/chat/completions", json={"model":"fixture"})
    assert not sent and control.rows[str(identifier)]["phase"] == "unknown"
    assert [event[0] for event in control.events].count("dispatch") == 1
    assert permit.closed is False


@pytest.mark.asyncio
async def test_unknown_usage_cancellation_and_provider_failure_keep_reservation():
    for failure in ("missing", "cancelled", "error"):
        control, budget, _ = setup()
        identifier=uuid4()
        await budget.on_chat_model_start({}, [[]], run_id=identifier)
        await budget.authorize(httpx.Request("POST","https://fixture.invalid/chat/completions",json={"model":"fixture"}))
        if failure == "missing":
            with pytest.raises(ModelBudgetError):
                await budget.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="no usage"))]]),run_id=identifier)
        else:
            await budget.on_llm_error(asyncio.CancelledError() if failure == "cancelled" else RuntimeError("private"),
                                      run_id=identifier,response=result())
        assert control.rows[str(identifier)]["phase"] == "unknown"
        assert "counts" not in control.rows[str(identifier)]


@pytest.mark.asyncio
async def test_lost_settlement_response_is_retried_without_repeating_model_and_zero_is_explicit():
    control, budget, _=setup();control.drop_settlement_response=True
    identifier=uuid4()
    await budget.on_chat_model_start({},[[]],run_id=identifier)
    await budget.authorize(httpx.Request("POST","https://fixture.invalid/chat/completions",json={"model":"fixture"}))
    with pytest.raises(ModelBudgetError):
        await budget.on_llm_end(result(0,0),run_id=identifier)
    await budget.close()
    assert [event[0] for event in control.events] == ["prepare","dispatch","settle","settle"]
    assert control.rows[str(identifier)]["counts"] == {"input_tokens":0,"output_tokens":0}


@pytest.mark.asyncio
async def test_missing_foreign_cached_or_invalid_context_never_gets_budget():
    control, budget, _=setup()
    request=httpx.Request("POST","https://fixture.invalid/chat/completions",json={"model":"fixture"})
    with pytest.raises(ModelBudgetError):
        await budget.authorize(request)
    identifier=uuid4()
    await budget.on_chat_model_start({},[[]],run_id=identifier)
    await budget.on_llm_end(result(),run_id=identifier)  # Cache hit, no physical HTTP.
    with pytest.raises(ModelBudgetError):
        await budget.authorize(request)
    _, foreign, _=setup()
    await foreign.on_chat_model_start({},[[]],run_id=uuid4())
    with pytest.raises(ModelBudgetError):
        await budget.authorize(request)
    for payload in ({"model":"other"},{"model":"fixture","n":2},{"model":"fixture","n":True}):
        await budget.on_chat_model_start({},[[]],run_id=uuid4())
        with pytest.raises(ModelBudgetError):
            await budget.authorize(httpx.Request("POST","https://fixture.invalid/chat/completions",json=payload))
    assert not control.events


@pytest.mark.asyncio
async def test_control_protocol_never_sends_declared_bound_and_rejects_implicit_zero():
    control, budget, _=setup();captured=[]
    def respond(request):
        import json
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={"permitted":True})
    client=ControlPlaneClient("http://127.0.0.1:18082","worker","w"*32,transport=httpx.MockTransport(respond))
    identifier=str(uuid4());sha="a"*64
    try:
        await client.model_budget(budget.lease,"dispatch",identifier,sha)
        for values in ({},{"input_tokens":True,"output_tokens":1},{"input_tokens":0,"output_tokens":-1}):
            with pytest.raises(ControlPlaneError):
                await client.model_budget(budget.lease,"settle",identifier,sha,**values)
        await client.model_budget(budget.lease,"settle",identifier,sha,input_tokens=0,output_tokens=0)
        assert set(captured[0]) == {"lease","callId","requestSha256"}
        assert captured[1]["inputTokens"] == captured[1]["outputTokens"] == 0
        assert "worker" not in captured[0]["lease"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_official_summarization_call_inherits_budget_and_has_its_own_receipt(tmp_path):
    from deepagents.backends import FilesystemBackend
    from deepagents.middleware.summarization import SummarizationMiddleware
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI
    control, budget, permit = setup()
    requests=[]
    async def respond(request):
        import json
        body=json.loads(request.content);requests.append(body)
        assert body["model"]=="fixture"
        return httpx.Response(200,json={"id":"chatcmpl-fixture","object":"chat.completion","created":1700000000,"model":"fixture",
            "choices":[{"index":0,"message":{"role":"assistant","content":"Synthetic summary or answer"},"finish_reason":"stop"}],
            "usage":{"prompt_tokens":20,"completion_tokens":5,"total_tokens":25}})
    async with httpx.AsyncClient(transport=PermitTransport(permit,httpx.MockTransport(respond),model_budget=budget)) as http:
        model=ChatOpenAI(model="fixture",api_key="synthetic-only",base_url="https://fixture.invalid/v1",http_async_client=http,max_retries=0)
        summarizer=SummarizationMiddleware(model=model,backend=FilesystemBackend(root_dir=tmp_path,virtual_mode=True),trigger=("messages",4),keep=("messages",2))
        agent=create_agent(model=model,tools=[],middleware=[summarizer])
        messages=[HumanMessage(content="old question"),AIMessage(content="old answer"),HumanMessage(content="earlier question"),
                  AIMessage(content="earlier answer"),HumanMessage(content="current question")]
        output=await agent.ainvoke({"messages":messages},config={"callbacks":[budget]})
        assert output["messages"][-1].content=="Synthetic summary or answer"
    assert len(requests)==len(control.rows)==2
    assert all(row["phase"]=="settled" for row in control.rows.values())
    assert sum(row["counts"]["input_tokens"] for row in control.rows.values())==40
    assert requests[0]["messages"] != requests[1]["messages"]
    assert list(tmp_path.rglob("*.md"))
    await budget.close()
