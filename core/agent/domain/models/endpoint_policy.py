"""Administrator-owned model egress policy, using HTTPX's public transport API.

An origin allowlist is deliberately separate from the address ranges: allowing a
subnet never permits arbitrary URLs in that subnet. DNS is resolved once per
request; the validated address is also the address passed to the socket layer.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import json
import os
import socket
import ssl
from urllib.parse import urlsplit

import httpx


class ModelEndpointDenied(ValueError):
    def __init__(self) -> None:
        # Never include an untrusted URL (possibly containing a credential).
        super().__init__("Model endpoint is not approved by the platform administrator")


def parse_endpoint(value: str) -> tuple[httpx.URL, str]:
    try:
        parsed = urlsplit(value)
        if (not value or any(ord(c) <= 32 for c in value) or "\\" in value
                or parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "%" in parsed.netloc):
            raise ModelEndpointDenied()
        url = httpx.URL(value)
        port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ModelEndpointDenied()
        host = url.host.lower().rstrip(".")
        if host != url.host.lower():
            raise ModelEndpointDenied()
        host_port = f"[{host}]" if ":" in host else host
        return url, f"{url.scheme}://{host_port}:{port}"
    except (ValueError, httpx.InvalidURL):
        raise ModelEndpointDenied() from None


@dataclass(frozen=True)
class EndpointPolicy:
    origins: frozenset[str]
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
    public_origins: frozenset[str] = frozenset()
    denied_addresses: frozenset[str] = frozenset()
    # Only a test may set this constructor argument; no environment switch exists.
    allow_loopback_for_tests: bool = False

    @classmethod
    def from_env(cls) -> EndpointPolicy:
        def values(name: str) -> list[str]:
            raw = json.loads(os.getenv(name, "[]"))
            if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
                raise ValueError(f"{name} must be a JSON array of strings")
            return raw

        origins = frozenset(parse_endpoint(x)[1] for x in values("MODEL_ALLOWED_ORIGINS"))
        public = frozenset(parse_endpoint(x)[1] for x in values("MODEL_PUBLIC_DEMO_ORIGINS"))
        if not public.issubset(origins):
            raise ValueError("Demo origins must also be explicitly approved")
        networks = tuple(ipaddress.ip_network(x, strict=True) for x in values("MODEL_ALLOWED_CIDRS"))
        blocked = set(values("MODEL_DENIED_ADDRESSES"))
        if os.getenv("KUBERNETES_SERVICE_HOST"):
            blocked.add(os.environ["KUBERNETES_SERVICE_HOST"])
        return cls(origins, networks, public, frozenset(blocked))

    def validate_url(self, value: str) -> tuple[httpx.URL, str]:
        url, origin = parse_endpoint(value)
        if origin not in self.origins:
            raise ModelEndpointDenied()
        if url.host in {"metadata.google.internal", "metadata", "kubernetes", "kubernetes.default"} or url.host.startswith("kubernetes.default.svc"):
            raise ModelEndpointDenied()
        return url, origin

    def validate_address(self, address: str, origin: str) -> None:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise ModelEndpointDenied() from None
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            raise ModelEndpointDenied()
        if (address in self.denied_addresses or address in {"100.100.100.200", "168.63.129.16"}
                or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved
                or (ip.is_loopback and not self.allow_loopback_for_tests)):
            raise ModelEndpointDenied()
        if ip.is_loopback and self.allow_loopback_for_tests:
            return
        if ip.is_global:
            if origin not in self.public_origins or not origin.startswith("https://"):
                raise ModelEndpointDenied()
        elif not any(ip.version == network.version and ip in network for network in self.networks):
            raise ModelEndpointDenied()

    async def resolve(self, value: str) -> tuple[httpx.URL, str, str]:
        url, origin = self.validate_url(value)
        try:
            ip = ipaddress.ip_address(url.host)
            addresses = [str(ip)]
        except ValueError:
            try:
                records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
                    url.host, url.port or (443 if url.scheme == "https" else 80), type=socket.SOCK_STREAM), timeout=10)
                addresses = list(dict.fromkeys(record[4][0] for record in records))
            except (OSError, TimeoutError):
                raise ModelEndpointDenied() from None
        if not addresses:
            raise ModelEndpointDenied()
        # Reject mixed safe/unsafe answers, rather than letting DNS order pick a bypass.
        for address in addresses:
            self.validate_address(address, origin)
        return url, origin, addresses[0]


class PinnedModelTransport(httpx.AsyncBaseTransport):
    def __init__(self, policy: EndpointPolicy, *, verify: ssl.SSLContext | bool = True) -> None:
        if verify is False:
            raise ValueError("Model TLS verification cannot be disabled")
        self.policy = policy
        self.verify = verify
        # Distinct origins must never share an IP-keyed TLS connection pool.
        self.transports: dict[str, httpx.AsyncHTTPTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url, origin, address = await self.policy.resolve(str(request.url))
        if request.method != "POST" or not url.path.endswith("/chat/completions"):
            raise ModelEndpointDenied()
        transport = self.transports.get(origin)
        if transport is None:
            transport = httpx.AsyncHTTPTransport(verify=self.verify, trust_env=False, retries=0,
                                                limits=httpx.Limits(max_connections=16, max_keepalive_connections=4))
            self.transports[origin] = transport
        headers = httpx.Headers(request.headers)
        headers["Host"] = url.netloc.decode("ascii")
        extensions = dict(request.extensions)
        extensions.pop("target", None)
        extensions["sni_hostname"] = url.host
        pinned = httpx.Request(request.method, url.copy_with(host=address), headers=headers,
                               stream=request.stream, extensions=extensions)
        response = await transport.handle_async_request(pinned)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise ModelEndpointDenied()
        return response

    async def aclose(self) -> None:
        await asyncio.gather(*(transport.aclose() for transport in self.transports.values()))
        self.transports.clear()


def model_http_client(*, policy: EndpointPolicy | None = None, timeout: float = 90,
                      ca_file: str | None = None) -> httpx.AsyncClient:
    if not 0 < timeout <= 1800:
        raise ValueError("Invalid model timeout")
    context = ssl.create_default_context(cafile=ca_file or os.getenv("MODEL_CA_FILE") or None)
    return httpx.AsyncClient(transport=PinnedModelTransport(policy or EndpointPolicy.from_env(), verify=context),
                             timeout=httpx.Timeout(timeout, connect=min(timeout, 10), pool=min(timeout, 10)),
                             trust_env=False, follow_redirects=False)
