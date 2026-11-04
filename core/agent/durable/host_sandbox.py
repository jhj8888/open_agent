"""Trusted demo-only Docker verification and physical sandbox reconciliation.

Official OpenSandbox handles lifecycle in the executor. This host-only adapter
independently observes Docker and the host policy; it never evaluates tenant code.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agent.durable.control import ControlPlaneClient
from agent.durable.supervision import ExecutionSnapshot, SupervisorClient
from agent.platform_runtime.sandbox import RunScope


class HostSandboxError(RuntimeError):
    pass


class DockerSandboxHost:
    NETWORK = 'open-agent-sandbox'
    BRIDGE = 'oa-sandbox0'
    RUNTIME = '/opt/open-agent/runtime/gvisor/20261102.0/runsc'
    SERVER = 'open-agent-opensandbox'

    def __init__(self, image: str, server_image: str):
        if not re.fullmatch(r'[A-Za-z0-9_./:-]+@sha256:[a-f0-9]{64}', image):
            raise HostSandboxError('HOST_IMAGE_INVALID')
        if not server_image.endswith('@sha256:68ca0212a2749b2c73096ce2ec0264455c64442c45f81007db442f52bf84c9d1'):
            raise HostSandboxError('HOST_SERVER_VERSION_INVALID')
        self.image, self.server_image = image, server_image

    async def command(self, *args: str, timeout: int = 10, maximum: int = 65536) -> str:
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        try:
            async with asyncio.timeout(timeout):
                output = b''
                while True:
                    chunk = await process.stdout.read(min(8192, maximum + 1 - len(output)))
                    if not chunk:
                        break
                    output += chunk
                    if len(output) > maximum:
                        raise HostSandboxError('HOST_SANDBOX_RESPONSE_INVALID')
                await process.wait()
            if process.returncode:
                raise HostSandboxError('HOST_SANDBOX_OPERATION_UNCONFIRMED')
            return output.decode('utf-8', errors='strict')
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

    async def server(self):
        # Pin behavior: a completed SDK create has left the internal port-retry
        # loop. An unknown create can still retry and must retain its reservation.
        server = json.loads(await self.command('docker', 'inspect', self.SERVER))[0]
        if (server['Config']['Image'] != self.server_image
                or server['Config']['Labels'].get('io.open-agent.component') != 'opensandbox-demo'):
            raise HostSandboxError('HOST_SERVER_VERSION_INVALID')

    async def policy(self):
        await self.server()
        configured = json.loads(Path('/etc/docker/daemon.json').read_bytes())['runtimes']['open-agent-runsc']
        if configured != {'path': self.RUNTIME, 'runtimeArgs': ['--platform=systrap', '--overlay2=all:memory']}:
            raise HostSandboxError('HOST_ISOLATION_UNVERIFIED')
        network = json.loads(await self.command('docker', 'network', 'inspect', self.NETWORK))[0]
        if (not network['Internal'] or network['EnableIPv6']
                or network['Labels'].get('io.open-agent.component') != 'opensandbox-demo'
                or network['Options'].get('com.docker.network.bridge.enable_icc') != 'false'
                or network['Options'].get('com.docker.network.bridge.name') != self.BRIDGE):
            raise HostSandboxError('HOST_NETWORK_UNVERIFIED')
        nameservers = [line.split()[1] for line in Path('/etc/resolv.conf').read_text().splitlines() if line.startswith('nameserver ')]
        if not nameservers or any(ipaddress.ip_address(value).is_loopback for value in nameservers):
            raise HostSandboxError('HOST_DNS_UNVERIFIED')
        for executable in ('iptables', 'ip6tables'):
            # Direct FORWARD guards also cover systems without an IPv6 DOCKER-USER chain.
            for direction in ('-i', '-o'):
                await self.command(executable, '-w', '5', '-C', 'FORWARD', direction, self.BRIDGE,
                    '-m', 'comment', '--comment', 'open-agent-sandbox-forward-deny', '-j', 'DROP')
            await self.command(executable, '-w', '5', '-C', 'INPUT', '-i', self.BRIDGE,
                '-m', 'conntrack', '!', '--ctstate', 'ESTABLISHED,RELATED', '-m', 'comment',
                '--comment', 'open-agent-sandbox-host-deny', '-j', 'DROP')
            await self.command(executable, '-w', '5', '-C', 'INPUT', '!', '-i', 'lo', '-p', 'tcp',
                '--dport', '46000:46499', '-m', 'conntrack', '!', '--ctstate', 'ESTABLISHED,RELATED',
                '-m', 'comment', '--comment', 'open-agent-sandbox-published-deny', '-j', 'DROP')

    @staticmethod
    def scope(snapshot: ExecutionSnapshot) -> RunScope:
        lease = snapshot.lease
        return RunScope(lease.scope.tenant, lease.scope.environment, UUID(lease.runId), lease.attempt,
                        UUID(lease.leaseId), snapshot.deadlineAt)

    async def entities(self, snapshot: ExecutionSnapshot) -> list[dict]:
        labels = self.scope(snapshot).metadata()
        arguments = ['docker', 'ps', '--all', '--quiet', '--no-trunc']
        for key, value in labels.items():
            arguments += ['--filter', 'label=' + key + '=' + value]
        identifiers = (await self.command(*arguments, maximum=4096)).split()
        if len(identifiers) > 1 or any(not re.fullmatch('[a-f0-9]{64}', value) for value in identifiers):
            raise HostSandboxError('HOST_SANDBOX_IDENTITY_AMBIGUOUS')
        if not identifiers:
            return []
        rows = json.loads(await self.command('docker', 'inspect', identifiers[0]))
        if len(rows) != 1 or rows[0]['Id'] != identifiers[0] or any(rows[0]['Config']['Labels'].get(k) != v for k, v in labels.items()):
            raise HostSandboxError('HOST_SANDBOX_IDENTITY_INVALID')
        return rows

    @staticmethod
    def sandbox_id(row: dict) -> str:
        name = row['Name']
        if not name.startswith('/sandbox-'):
            raise HostSandboxError('HOST_SANDBOX_IDENTITY_INVALID')
        value = name[len('/sandbox-'):]
        if str(UUID(value)) != value:
            raise HostSandboxError('HOST_SANDBOX_IDENTITY_INVALID')
        return value

    async def admit(self, snapshot: ExecutionSnapshot, sandbox_id: str):
        await self.policy()
        rows = await self.entities(snapshot)
        if len(rows) != 1 or self.sandbox_id(rows[0]) != sandbox_id:
            raise HostSandboxError('HOST_SANDBOX_IDENTITY_INVALID')
        row = rows[0]
        host = row['HostConfig']
        if (not row['State']['Running'] or row['Config']['Image'] != self.image
                or host['Runtime'] != 'open-agent-runsc' or host['Privileged']
                or host['NetworkMode'] != self.NETWORK or set(row['NetworkSettings']['Networks']) != {self.NETWORK}
                or host['PidMode'] == 'host' or host['IpcMode'] == 'host' or host.get('UTSMode') == 'host'
                or host['Binds'] or host['Devices'] or host['CapAdd'] or row['Mounts'] or host.get('DeviceRequests')
                or not 0 < host['Memory'] <= 512 * 1024 * 1024 or not 0 < host['NanoCpus'] <= 1_000_000_000
                or not 0 < host['PidsLimit'] <= 256 or host['RestartPolicy']['Name'] not in {'', 'no'}
                or 'no-new-privileges' not in ' '.join(host['SecurityOpt'])
                or not {'NET_ADMIN', 'NET_RAW', 'SYS_ADMIN', 'SYS_PTRACE'}.issubset(set(host['CapDrop']))):
            raise HostSandboxError('HOST_ISOLATION_UNVERIFIED')
        # Only reviewed fixed initialization, before staging/executing any tenant file.
        # Production images pre-create this UID during their reviewed build.
        await self.command('docker', 'exec', '--user', '0:0', row['Id'], '/bin/sh', '-c',
            'set -eu; if ! id agent >/dev/null 2>&1; then useradd --uid 1000 --user-group --no-create-home --home-dir /workspace --shell /usr/sbin/nologin agent; fi; test "$(id -u agent)" = 1000; test "$(id -g agent)" = 1000')

    async def absent(self, snapshot: ExecutionSnapshot):
        if await self.entities(snapshot):
            raise HostSandboxError('HOST_SANDBOX_STILL_PRESENT')
        if snapshot.sandboxId is not None:
            # Detect a same-name container with wrong/missing labels rather than
            # interpreting a filtered empty listing as physical absence.
            found = await self.command('docker', 'ps', '--all', '--quiet', '--no-trunc',
                                       '--filter', 'name=^/sandbox-' + snapshot.sandboxId + '$', maximum=4096)
            if found.strip():
                raise HostSandboxError('HOST_SANDBOX_IDENTITY_INVALID')

    async def cleanup(self, api: SupervisorClient, snapshot: ExecutionSnapshot):
        if snapshot.state not in {'failed', 'cancelled', 'timed_out', 'succeeded'}:
            raise HostSandboxError('HOST_CLEANUP_BEFORE_TERMINAL')
        if snapshot.sandboxPhase is None:
            await self.absent(snapshot)
            return snapshot, {'sandboxAbsent': True, 'creationSettled': True, 'reason': 'no_sandbox_creation_authorized'}
        if snapshot.isolationProfile != 'gvisor-demo' or snapshot.runtimeImage != self.image:
            raise HostSandboxError('HOST_SANDBOX_PROFILE_INVALID')
        await self.server()
        rows = await self.entities(snapshot)
        if snapshot.sandboxPhase == 'reserved':
            if rows or snapshot.sandboxId is not None:
                raise HostSandboxError('HOST_SANDBOX_IDENTITY_INVALID')
            return snapshot, {'sandboxAbsent': True, 'creationSettled': True, 'reason': 'creation_never_authorized'}
        if snapshot.sandboxId is None:
            # OpenSandbox 1.1.0 may recreate the same sandbox UUID with a new
            # Docker ID on a port conflict. Observation alone cannot prove the
            # server's background creation thread settled. Independent draining
            # of the creation service is required before unknown adoption.
            raise HostSandboxError('HOST_SANDBOX_CREATION_UNKNOWN')
        removed = None
        if rows:
            row = rows[0]
            if self.sandbox_id(row) != snapshot.sandboxId:
                raise HostSandboxError('HOST_SANDBOX_IDENTITY_INVALID')
            removed = row['Id']
            # Full immutable Docker ID, never name reuse, and no user-supplied arguments.
            await self.command('docker', 'rm', '--force', removed, timeout=20)
        await self.absent(snapshot)
        return snapshot, {'sandboxAbsent': True, 'creationSettled': True, 'sandboxId': snapshot.sandboxId,
                          'removedContainerId': removed, 'reason': 'acknowledged_creation_entity_absent',
                          'serverImage': self.server_image, 'observedAt': datetime.now(timezone.utc).isoformat()}


class BeforeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile: Literal['gvisor-demo']
    image: Annotated[str, Field(max_length=512)]


class AfterRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile: Literal['gvisor-demo']
    sandboxId: UUID


def read_capability(state: Path, lease_id: str) -> str:
    path = state / lease_id / 'worker.json'
    if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077 or path.stat().st_size > 65536:
        raise ValueError()
    settings = json.loads(path.read_bytes())
    return settings['sandbox']['admission_capability']


def admission_app(api: SupervisorClient, worker: ControlPlaneClient, host: DockerSandboxHost, state: Path) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    async def verify(lease_id: str, request: Request, phase: str):
        try:
            if str(UUID(lease_id)) != lease_id:
                raise ValueError()
            token = request.headers.get('X-Execution-Capability', '')
            expected = read_capability(state, lease_id)
            if len(token) > 128 or not secrets.compare_digest(token, expected):
                raise ValueError()
            raw = b''
            async for chunk in request.stream():
                raw += chunk
                if len(raw) > 2048:
                    raise ValueError()
            payload = (BeforeRequest if phase == 'before' else AfterRequest).model_validate_json(raw)
            snapshot = await api.inspect(lease_id)
            if snapshot.lease.scope.environment != 'test' or snapshot.state != 'running' or snapshot.phase not in {'launching', 'observed'}:
                raise ValueError()
            lease = snapshot.lease.attempt_lease()
            if not await worker.permit(lease):
                raise ValueError()
            if phase == 'before':
                if payload.image != host.image or snapshot.sandboxPhase is not None:
                    raise ValueError()
                await host.policy()
            else:
                if (snapshot.sandboxPhase != 'created' or snapshot.sandboxId != str(payload.sandboxId)
                        or snapshot.isolationProfile != payload.profile or snapshot.runtimeImage != host.image):
                    raise ValueError()
                await host.admit(snapshot, str(payload.sandboxId))
            if not await worker.permit(lease):
                raise ValueError()
            return JSONResponse({'verified': True})
        except Exception:
            # Validation errors contain input; never serialize or log them.
            return JSONResponse({'verified': False}, status_code=403)

    @app.post('/v1/attempts/{lease_id}/before')
    async def before(lease_id: str, request: Request):
        return await verify(lease_id, request, 'before')

    @app.post('/v1/attempts/{lease_id}/after')
    async def after(lease_id: str, request: Request):
        return await verify(lease_id, request, 'after')

    return app
