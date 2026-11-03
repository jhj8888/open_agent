import asyncio
import ipaddress

import httpx
from langchain_core.messages import HumanMessage, ToolMessage
import pytest

from agent.domain.models.endpoint_policy import EndpointPolicy, ModelEndpointDenied, PinnedModelTransport, model_http_client, parse_endpoint
from agent.domain.models.failures import model_failure
from agent.domain.models.providers import create_provider_model
from .test_server import endpoint, fixture_module  # shared real TCP contract fixture


def policy_for(endpoint):
    return EndpointPolicy(frozenset({parse_endpoint(endpoint)[1]}), (), allow_loopback_for_tests=True)


async def model(endpoint, name="contract-text", **kwargs):
    return await create_provider_model(model_name=name, base_url=endpoint, provider="openai",
        api_key=fixture_module.FIXTURE_TOKEN, http_client=model_http_client(policy=policy_for(endpoint)), **kwargs)


@pytest.mark.asyncio
async def test_platform_adapter_text_stream_and_usage(endpoint):
    handle = await model(endpoint)
    try:
        message = await handle.model.ainvoke([HumanMessage(content="test")])
        assert message.content == "Contract response"
        assert message.usage_metadata["total_tokens"] == 18
        chunks = [item async for item in handle.model.astream("test")]
        assert "".join(str(item.content) for item in chunks) == "Contract response"
        assert sum((item.usage_metadata or {}).get("total_tokens", 0) for item in chunks) == 18
    finally:
        await handle.aclose()
    assert handle.http_client.is_closed


@pytest.mark.asyncio
async def test_platform_adapter_tools_and_unknown_usage(endpoint):
    handle = await model(endpoint, "contract-tools")
    tool = {"type": "function", "function": {"name": "add_numbers", "description": "Add two integers", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}}
    try:
        configured = handle.model.bind_tools([tool])
        first = await configured.ainvoke("add 2 and 3")
        assert first.tool_calls[0]["args"] == {"a": 2, "b": 3}
        final = await configured.ainvoke([HumanMessage(content="add 2 and 3"), first, ToolMessage(content="5", tool_call_id=first.tool_calls[0]["id"])])
        assert final.content == "5"
    finally:
        await handle.aclose()
    unknown = await model(endpoint, "contract-no-usage")
    try:
        assert (await unknown.model.ainvoke("test")).usage_metadata is None
    finally:
        await unknown.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,code", [("contract-unauthorized", "MODEL_AUTH_FAILED"), ("contract-rate-limit", "MODEL_RATE_LIMITED"), ("contract-timeout", "MODEL_TIMEOUT"), ("contract-malformed-sse", "MODEL_PROTOCOL_ERROR"), ("contract-disconnect", "MODEL_CONNECTION_FAILED"), ("contract-redirect", "MODEL_ENDPOINT_DENIED")])
async def test_platform_errors_do_not_retry_or_expose_credentials(endpoint, name, code):
    handle = await model(endpoint, name, timeout=0.2 if name == "contract-timeout" else 5)
    before = fixture_module.app.state.calls
    try:
        with pytest.raises(Exception) as caught:
            _ = [chunk async for chunk in handle.model.astream("test")]
        failure = model_failure(caught.value)
        assert failure.code == code
        assert fixture_module.FIXTURE_TOKEN not in repr(failure)
        assert fixture_module.app.state.calls == before + 1
    finally:
        await handle.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("parameter", ["api_key", "base_url", "model", "http_async_client", "default_headers", "model_kwargs", "extra_body", "max_retries"])
async def test_generation_parameters_cannot_override_connection(endpoint, parameter):
    client = model_http_client(policy=policy_for(endpoint))
    with pytest.raises(ValueError, match="platform-owned"):
        await create_provider_model(model_name="contract-text", base_url=endpoint, provider="openai", api_key="private",
                                    http_client=client, parameters={parameter: "bad"})
    assert client.is_closed


@pytest.mark.parametrize("url", ["http://user:password@llm.local/v1", "file:///etc/passwd", "http://llm.local/v1?token=secret", "http://llm.local/#x", "http://llm.local./v1", "http://llm.local:0/v1", "http://llm.local\\@evil/v1", "http://llm.local/\nsecret", "http://[fe80::1%25eth0]/v1"])
def test_endpoint_syntax_is_strict_and_error_is_redacted(url):
    with pytest.raises(ModelEndpointDenied) as caught:
        parse_endpoint(url)
    assert url not in str(caught.value)


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "0.0.0.0", "169.254.169.254", "100.100.100.200", "168.63.129.16", "224.0.0.1", "::ffff:10.1.2.3", "8.8.8.8", "10.9.0.1"])
def test_address_denial_even_when_origin_is_allowed(address):
    policy = EndpointPolicy(frozenset({"http://llm.local:80"}), (ipaddress.ip_network("10.0.0.0/8"),), denied_addresses=frozenset({"10.9.0.1"}))
    with pytest.raises(ModelEndpointDenied):
        policy.validate_address(address, "http://llm.local:80")


@pytest.mark.asyncio
async def test_dns_rebinding_uses_only_validated_ip(monkeypatch):
    loop = asyncio.get_running_loop()
    addresses = ["10.1.2.3"]
    calls = []
    async def resolve(*args, **kwargs):
        calls.append(args)
        return [(2, 1, 6, "", (item, 443)) for item in addresses]
    monkeypatch.setattr(loop, "getaddrinfo", resolve)
    policy = EndpointPolicy(frozenset({"https://llm.local:443"}), (ipaddress.ip_network("10.0.0.0/8"),))
    seen = []
    async def send(request):
        seen.append(request)
        return httpx.Response(200, json={})
    transport = PinnedModelTransport(policy)
    transport.transports["https://llm.local:443"] = httpx.MockTransport(send)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post("https://llm.local/v1/chat/completions")
        assert len(calls) == 1
        assert seen[0].url.host == "10.1.2.3"
        assert seen[0].headers["host"] == "llm.local"
        assert seen[0].extensions["sni_hostname"] == "llm.local"
        addresses[:] = ["10.1.2.3", "169.254.169.254"]
        with pytest.raises(ModelEndpointDenied):
            await client.post("https://llm.local/v1/chat/completions")
        assert len(seen) == 1


def test_environment_policy_is_deny_by_default(monkeypatch):
    for name in ("MODEL_ALLOWED_ORIGINS", "MODEL_PUBLIC_DEMO_ORIGINS", "MODEL_ALLOWED_CIDRS", "MODEL_DENIED_ADDRESSES"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ModelEndpointDenied):
        EndpointPolicy.from_env().validate_url("https://api.deepseek.com/v1")


@pytest.mark.asyncio
async def test_real_deepagents_tool_execution_never_receives_model_credentials(endpoint):
    from deepagents import create_deep_agent
    from langchain_core.tools import tool

    observed = []
    @tool
    def add_numbers(a: int, b: int) -> str:
        """Add two integers for the synthetic contract test."""
        observed.append({"a": a, "b": b})
        return str(a + b)

    handle = await model(endpoint, "contract-tools")
    try:
        graph = create_deep_agent(model=handle.model, tools=[add_numbers])
        result = await graph.ainvoke({"messages": [{"role": "user", "content": "Add 2 and 3 using add_numbers."}]}, config={"recursion_limit": 20})
        assert observed == [{"a": 2, "b": 3}]
        assert result["messages"][-1].content == "5"
        assert fixture_module.FIXTURE_TOKEN not in repr(observed) + repr(result["messages"])
    finally:
        await handle.aclose()
