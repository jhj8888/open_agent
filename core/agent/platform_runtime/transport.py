"""Restrict every official SDK request to the administrator's sandbox service origin."""

from __future__ import annotations

import ssl
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from opensandbox.config import ConnectionConfig
from opensandbox.transport import RetryPolicy

from agent.platform_runtime.sandbox import SandboxFailure


class BoundedResponse(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, limit: int = 8 * 1024 * 1024):
        self.stream, self.limit = stream, limit

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.limit:
                await self.stream.aclose()
                raise SandboxFailure("SANDBOX_RESPONSE_LIMIT")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class SandboxServiceTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        endpoint: str,
        *,
        ca_file: str | None = None,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(char in endpoint for char in "\r\n\t\\%")
        ):
            raise SandboxFailure("SANDBOX_ENDPOINT_INVALID")
        try:
            self.origin = (
                parsed.scheme,
                parsed.hostname.lower(),
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
        except ValueError:
            raise SandboxFailure("SANDBOX_ENDPOINT_INVALID") from None
        self.inner = inner or httpx.AsyncHTTPTransport(
            verify=ssl.create_default_context(cafile=ca_file),
            trust_env=False,
            retries=0,
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if (
            url.scheme,
            url.host.lower(),
            url.port or (443 if url.scheme == "https" else 80),
        ) != self.origin:
            raise SandboxFailure("SANDBOX_ENDPOINT_DENIED")
        response = await self.inner.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise SandboxFailure("SANDBOX_REDIRECT_DENIED")
        # Never allow response decompression to turn a small body into unbounded memory.
        if response.headers.get("content-encoding", "identity").lower() not in {
            "",
            "identity",
        }:
            await response.aclose()
            raise SandboxFailure("SANDBOX_RESPONSE_ENCODING")
        response.stream = BoundedResponse(response.stream)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


def sandbox_connection(
    endpoint: str, credential: str, *, ca_file: str | None = None
) -> ConnectionConfig:
    if len(credential) < 32 or any(char in credential for char in "\r\n\0"):
        raise SandboxFailure("SANDBOX_CREDENTIAL_INVALID")
    transport = SandboxServiceTransport(endpoint, ca_file=ca_file)
    parsed = urlsplit(endpoint)
    return ConnectionConfig(
        domain=parsed.netloc,
        protocol=parsed.scheme,
        api_key=credential,
        debug=False,
        use_server_proxy=True,
        request_timeout=timedelta(seconds=30),
        transport=transport,
        retry_policy=RetryPolicy.disabled(),
        headers={"Accept-Encoding": "identity"},
    )
