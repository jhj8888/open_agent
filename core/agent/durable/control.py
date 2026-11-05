"""Bounded private control-plane client. Tenant code never receives this client/key."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from collections.abc import AsyncIterator
from urllib.parse import urlsplit
import ipaddress
import json
import re
import ssl
from uuid import UUID

import httpx

from agent.durable.contracts import RunRef


class ControlPlaneError(RuntimeError):
    def __init__(self, code: str, category: str = "unspecified") -> None:
        self.code = code
        self.category = category if category in {
            "connect_timeout", "read_timeout", "write_timeout", "pool_timeout",
            "total_timeout", "transport", "response_format", "unspecified",
        } else "unspecified"
        super().__init__(code)


@dataclass(frozen=True)
class AttemptLease:
    run: RunRef
    attempt: int
    lease_id: str
    fencing_token: int

    def __post_init__(self) -> None:
        if (
            type(self.attempt) is not int
            or not 1 <= self.attempt <= 100
            or type(self.fencing_token) is not int
            or self.fencing_token < 1
            or str(UUID(self.lease_id)) != self.lease_id
        ):
            raise ValueError("Invalid execution lease")

    def body(self) -> dict[str, Any]:
        return {
            "tenant": self.run.tenant,
            "environment": self.run.environment,
            "attempt": self.attempt,
            "leaseId": self.lease_id,
            "fencingToken": self.fencing_token,
        }


@dataclass(frozen=True)
class Delivery:
    id: int
    run: RunRef
    operation: str
    claim_id: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.id) is not int
            or self.id < 1
            or self.operation not in {"start", "cancel"}
            or str(UUID(self.claim_id)) != self.claim_id
        ):
            raise ValueError("Invalid outbox delivery")


class ControlPlaneClient:
    TOTAL_TIMEOUT_SECONDS = 10

    def __init__(
        self,
        endpoint: str,
        key_id: str,
        key: str,
        *,
        ca_file: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        parsed = urlsplit(endpoint)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            loopback = False
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or (parsed.scheme == "http" and not loopback)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(ord(char) <= 32 or char in "\\%" for char in endpoint)
            or len(key) < 32
            or len(key) > 256
            or any(ord(char) <= 32 for char in key)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key_id)
        ):
            raise ValueError("Invalid private control-plane configuration")
        self.http = httpx.AsyncClient(
            base_url=endpoint.rstrip("/"),
            transport=transport,
            verify=ssl.create_default_context(cafile=ca_file),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(10, connect=5),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=8),
            headers={
                "X-Platform-Key-Id": key_id,
                "X-Platform-Service-Key": key,
                "Accept-Encoding": "identity",
            },
        )

    async def request(
        self,
        path: str,
        body: dict[str, Any],
        *,
        limit: int = 1024 * 1024,
        raw: bool = False,
    ) -> Any:
        if (
            not path.startswith("/internal/platform/")
            or ".." in path
            or "//" in path
            or any(ord(char) <= 32 or char in "?\\#%" for char in path)
        ):
            raise ControlPlaneError("CONTROL_PATH_INVALID")
        try:
            async with asyncio.timeout(self.TOTAL_TIMEOUT_SECONDS), self.http.stream(
                "POST", path, json=body
            ) as response:
                if response.status_code != 200:
                    # Never echo the response body or httpx exceptions, which may carry secrets.
                    raise ControlPlaneError(f"CONTROL_HTTP_{response.status_code}")
                if response.headers.get("content-encoding", "identity") not in {
                    "",
                    "identity",
                }:
                    raise ControlPlaneError("CONTROL_RESPONSE_INVALID")
                chunks: list[bytes] = []
                total = 0
                # Only identity encoding is accepted above; this also supports
                # transports that already buffered their (still bounded) body.
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise ControlPlaneError("CONTROL_RESPONSE_LIMIT")
                    chunks.append(chunk)
                data = b"".join(chunks)
                return data if raw else json.loads(data)
        except ControlPlaneError:
            raise
        except (httpx.HTTPError, ValueError, UnicodeError, TimeoutError) as error:
            if isinstance(error, httpx.ConnectTimeout):
                category = "connect_timeout"
            elif isinstance(error, httpx.ReadTimeout):
                category = "read_timeout"
            elif isinstance(error, httpx.WriteTimeout):
                category = "write_timeout"
            elif isinstance(error, httpx.PoolTimeout):
                category = "pool_timeout"
            elif isinstance(error, TimeoutError):
                category = "total_timeout"
            elif isinstance(error, httpx.HTTPError):
                category = "transport"
            else:
                category = "response_format"
            raise ControlPlaneError("CONTROL_UNAVAILABLE", category) from None

    async def deliveries(self, limit: int = 10) -> list[Delivery]:
        data = await self.request(
            "/internal/platform/dispatch/claim", {"limit": limit, "leaseSeconds": 60}
        )
        return [
            Delivery(
                row["id"],
                RunRef(row["tenant"], row["environment"], row["runId"]),
                row["operation"],
                row["claimId"],
            )
            for row in data
        ]

    async def output(self, lease: AttemptLease, descriptor: dict, content: AsyncIterator[bytes]) -> dict:
        """Reserve metadata then stream bytes; model/tenant code never gets this credential."""
        row = await self.request(f"/internal/platform/worker/runs/{lease.run.run_id}/outputs",
                                 {"lease":lease.body(), **descriptor})
        if row.get("state")=="available":
            return row
        identifier=str(UUID(row["output_id"]))
        headers={"Content-Type":"application/octet-stream", "Content-Length":str(descriptor["size"]),
                 "X-Output-Tenant":lease.run.tenant, "X-Output-Environment":lease.run.environment,
                 "X-Output-Attempt":str(lease.attempt), "X-Output-Lease":lease.lease_id,
                 "X-Output-Fence":str(lease.fencing_token)}
        try:
            async with asyncio.timeout(90), self.http.stream("POST",
                    f"/internal/platform/worker/runs/{lease.run.run_id}/outputs/{identifier}/content",
                    headers=headers,content=content,timeout=httpx.Timeout(60,connect=5)) as response:
                if response.status_code!=200:
                    raise ControlPlaneError(f"CONTROL_HTTP_{response.status_code}")
                data=bytearray()
                if response.headers.get("content-encoding","identity") not in {"","identity"}:
                    raise ControlPlaneError("CONTROL_RESPONSE_INVALID")
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>16384:
                        raise ControlPlaneError("CONTROL_RESPONSE_LIMIT")
                result=json.loads(data)
                if result.get("output_id")!=identifier or result.get("state")!="available":
                    raise ControlPlaneError("CONTROL_RESPONSE_INVALID")
                return result
        except (httpx.HTTPError,ValueError,TimeoutError):
            # The next reserve may discover an already committed publication.
            raise ControlPlaneError("OUTPUT_UPLOAD_UNAVAILABLE") from None

    async def acknowledge(self, delivery: Delivery) -> bool:
        result = await self.request(
            f"/internal/platform/dispatch/{delivery.id}/ack",
            {"claimId": delivery.claim_id},
        )
        return result["accepted"] is True

    async def retry_later(self, delivery: Delivery) -> bool:
        result = await self.request(
            f"/internal/platform/dispatch/{delivery.id}/retry",
            {"claimId": delivery.claim_id, "delaySeconds": 5},
        )
        return result["accepted"] is True

    async def state(self, run: RunRef, *, dispatcher: bool = False) -> dict[str, Any]:
        role = "dispatch" if dispatcher else "worker"
        return await self.request(
            f"/internal/platform/{role}/runs/{run.run_id}/status",
            {"tenant": run.tenant, "environment": run.environment},
        )

    async def claim(self, run: RunRef) -> AttemptLease | None:
        row = await self.request(
            f"/internal/platform/worker/runs/{run.run_id}/claim",
            {"tenant": run.tenant, "environment": run.environment},
        )
        if row["lease"] is None:
            return None
        lease = row["lease"]
        return AttemptLease(
            run, lease["attempt"], lease["leaseId"], lease["fencingToken"]
        )

    async def heartbeat(self, lease: AttemptLease) -> bool:
        row = await self.request(
            f"/internal/platform/worker/runs/{lease.run.run_id}/heartbeat", lease.body()
        )
        return row["permitted"] is True

    async def permit(self, lease: AttemptLease) -> bool:
        row = await self.request(
            f"/internal/platform/worker/runs/{lease.run.run_id}/permit", lease.body()
        )
        return row["permitted"] is True

    async def append(
        self, lease: AttemptLease, key: str, kind: str, payload: dict[str, Any]
    ) -> None:
        await self.request(
            f"/internal/platform/worker/runs/{lease.run.run_id}/events",
            {
                "lease": lease.body(),
                "eventKey": key,
                "eventType": kind,
                "payload": payload,
            },
        )

    async def finish(
        self, lease: AttemptLease, state: str, code: str | None = None
    ) -> dict[str, Any]:
        return await self.request(
            f"/internal/platform/worker/runs/{lease.run.run_id}/finish",
            {"lease": lease.body(), "state": state, "errorCode": code},
        )

    async def runtime(self, lease: AttemptLease):
        from agent.durable.runtime_config import RuntimeBundle

        path = f"/internal/platform/worker/runs/{lease.run.run_id}/runtime"
        try:
            data = await self.request(path, lease.body())
        except ControlPlaneError as error:
            if error.code != "CONTROL_UNAVAILABLE" or error.category not in {
                "connect_timeout", "read_timeout", "write_timeout", "pool_timeout", "total_timeout", "transport",
            }:
                raise
            # This endpoint only reads a lease-bound snapshot. Do not generalize
            # this retry to model/tool calls or mutations with uncertain outcomes.
            if not await self.heartbeat(lease):
                raise ControlPlaneError("CONTROL_LEASE_FENCED") from None
            print(json.dumps({"event": "control.runtime_retry", "run_id": lease.run.run_id,
                              "category": error.category}), flush=True)
            data = await self.request(path, lease.body())
        try:
            return RuntimeBundle.model_validate(data)
        except ValueError:
            raise ControlPlaneError("CONTROL_RUNTIME_INVALID") from None

    async def artifact(
        self, lease: AttemptLease, release_id: str, index: int, maximum: int
    ) -> bytes:
        if (
            str(UUID(release_id)) != release_id
            or not -1 <= index <= 99
            or not 0 <= maximum <= 5 * 1024 * 1024
        ):
            raise ControlPlaneError("CONTROL_ARTIFACT_INVALID")
        return await self.request(
            f"/internal/platform/worker/runs/{lease.run.run_id}/artifact",
            {"lease": lease.body(), "releaseId": release_id, "fileIndex": index},
            limit=maximum,
            raw=True,
        )

    async def aclose(self) -> None:
        await self.http.aclose()
