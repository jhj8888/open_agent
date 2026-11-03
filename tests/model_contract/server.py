"""Deterministic, offline OpenAI-compatible contract server. It never calls a model.

Start with the agent Python environment: python tests/model_contract/server.py
The public fixture token is only for tests; no real credentials belong here.
"""
import asyncio
import json
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="open_agent model contract fixture")
app.state.calls = 0
app.state.calls_by_model = {}
FIXTURE_TOKEN = "contract-test-key"


def response(model: str, message: dict[str, Any], *, usage: bool = True) -> dict[str, Any]:
    result = {
        "id": "chatcmpl-contract",
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
    }
    if usage:
        result["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    return result


def chunk(model: str, delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-contract",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def event(payload: dict[str, Any]) -> bytes:
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": "synthetic-contract-fixture"}


@app.post("/v1/chat/completions")
async def completion(request: Request):
    app.state.calls += 1
    if request.headers.get("authorization") != "Bearer " + FIXTURE_TOKEN:
        return JSONResponse({"error": {"message": "Fixture authentication failed", "type": "invalid_api_key"}}, status_code=401)
    body = await request.json()
    model = body.get("model", "contract-text")
    app.state.calls_by_model[model] = app.state.calls_by_model.get(model, 0) + 1
    if model == "contract-unauthorized":
        return JSONResponse({"error": {"message": "Synthetic authentication failure", "type": "invalid_api_key"}}, status_code=401)
    if model == "contract-rate-limit":
        return JSONResponse({"error": {"message": "Synthetic rate limit", "type": "rate_limit_error"}}, status_code=429, headers={"Retry-After": "1"})
    if model == "contract-timeout":
        await asyncio.sleep(10)
    if model == "contract-redirect":
        # Explicitly dangerous target for redirect rejection tests; the fixture never connects to it.
        return JSONResponse({}, status_code=307, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
    messages = body.get("messages", [])
    has_tool_result = any(message.get("role") == "tool" for message in messages)
    tool_mode = model == "contract-tools" and not has_tool_result
    if tool_mode:
        tools = body.get("tools", [])
        name = tools[0]["function"]["name"] if tools else "add_numbers"
        if any(tool.get("function", {}).get("name") == "add_numbers" for tool in tools):
            name = "add_numbers"
        choice = body.get("tool_choice")
        if isinstance(choice, dict):
            name = choice.get("function", {}).get("name", name)
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call-contract-1", "type": "function", "function": {"name": name, "arguments": '{"a":2,"b":3}'}}]}
    else:
        message = {"role": "assistant", "content": "5" if has_tool_result else "Contract response"}
    usage = model != "contract-no-usage"
    if not body.get("stream"):
        return JSONResponse(response(model, message, usage=usage))

    async def stream():
        yield event(chunk(model, {"role": "assistant"}))
        if model == "contract-malformed-sse":
            yield b'data: {"malformed":\n\n'
            return
        if model == "contract-disconnect":
            yield event(chunk(model, {"content": "partial"}))
            raise ConnectionResetError("Synthetic stream interruption")
        if tool_mode:
            call = message["tool_calls"][0]
            yield event(chunk(model, {"tool_calls": [{"index": 0, "id": call["id"], "type": "function", "function": {"name": call["function"]["name"], "arguments": '{"a":2,'}}]}))
            yield event(chunk(model, {"tool_calls": [{"index": 0, "function": {"arguments": '"b":3}'}}]}))
        else:
            for word in [message["content"][:4], message["content"][4:]]:
                if word:
                    yield event(chunk(model, {"content": word}))
        yield event(chunk(model, {}, "tool_calls" if tool_mode else "stop"))
        if usage and body.get("stream_options", {}).get("include_usage"):
            last = chunk(model, {})
            last["choices"] = []
            last["usage"] = response(model, message)["usage"]
            yield event(last)
        yield b"data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/stats")
async def stats():
    return {"calls": app.state.calls, "models": app.state.calls_by_model}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MOCK_MODEL_PORT", "18800")))
