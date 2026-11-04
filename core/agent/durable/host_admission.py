"""Executor-side access to the trusted host isolation verifier over a Unix socket."""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath

import httpx

from agent.durable.control import AttemptLease
from agent.platform_runtime.sandbox import RunScope, SandboxFailure


class HostAdmissionClient:
    def __init__(self, lease: AttemptLease, socket_path: str, capability: str, *, transport=None):
        if (PurePosixPath(socket_path) != PurePosixPath('/run/open-agent/supervisor.sock')
                or len(capability) < 32 or len(capability) > 128
                or any(not (char.isascii() and (char.isalnum() or char in '_-')) for char in capability)):
            raise SandboxFailure('HOST_ADMISSION_CONFIGURATION_INVALID')
        self.lease = lease
        self.http = httpx.AsyncClient(
            base_url='http://supervisor',
            transport=transport or httpx.AsyncHTTPTransport(uds=socket_path, retries=0),
            headers={'X-Execution-Capability': capability, 'Accept-Encoding': 'identity'},
            trust_env=False, follow_redirects=False, timeout=10,
        )

    async def _verify(self, scope: RunScope, phase: str, payload: dict):
        if (scope.tenant_id != self.lease.run.tenant or scope.environment != self.lease.run.environment
                or str(scope.run_id) != self.lease.run.run_id or str(scope.lease_id) != self.lease.lease_id
                or scope.attempt != self.lease.attempt):
            raise SandboxFailure('HOST_ADMISSION_SCOPE_MISMATCH')
        scope.remaining()
        try:
            async with asyncio.timeout(15), self.http.stream(
                'POST', f'/v1/attempts/{self.lease.lease_id}/{phase}', json=payload
            ) as response:
                if response.status_code != 200 or response.headers.get('content-encoding', 'identity') != 'identity':
                    raise SandboxFailure('HOST_ISOLATION_UNVERIFIED')
                body = b''
                async for part in response.aiter_bytes():
                    body += part
                    if len(body) > 1024:
                        raise SandboxFailure('HOST_ISOLATION_UNVERIFIED')
                import json
                result = json.loads(body)
                if result != {'verified': True} or type(result['verified']) is not bool:
                    raise SandboxFailure('HOST_ISOLATION_UNVERIFIED')
        except SandboxFailure:
            raise
        except (httpx.HTTPError, ValueError, TimeoutError):
            raise SandboxFailure('HOST_ISOLATION_UNVERIFIED') from None

    async def before_create(self, scope: RunScope, profile: str, image: str):
        await self._verify(scope, 'before', {'profile': profile, 'image': image})

    async def after_create(self, scope: RunScope, profile: str, sandbox_id: str):
        await self._verify(scope, 'after', {'profile': profile, 'sandboxId': sandbox_id})

    async def aclose(self):
        await self.http.aclose()
