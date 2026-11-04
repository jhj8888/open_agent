"""Per-attempt OpenSandbox lifecycle using the official 1.1.0 SDK.

The deployment supplies live isolation admission and a durable lease journal.
This adapter has no host-execution fallback and never puts model/API keys in a sandbox.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import secrets
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from uuid import UUID

from opensandbox.config import ConnectionConfig
from opensandbox.manager import SandboxManager
from opensandbox.models.execd import ExecutionHandlers, RunCommandOpts
from opensandbox.models.filesystem import WriteEntry
from opensandbox.models.sandboxes import NetworkPolicy, SandboxFilter
from opensandbox.sandbox import Sandbox

from agent.platform_runtime.artifacts import VerifiedRelease


class SandboxFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RunScope:
    tenant_id: str
    environment: Literal["test", "prod"]
    run_id: UUID
    attempt: int
    lease_id: UUID
    deadline: datetime

    def remaining(self) -> int:
        if (
            not self.tenant_id
            or len(self.tenant_id) > 80
            or self.environment not in {"test", "prod"}
            or not 1 <= self.attempt <= 100
            or self.deadline.tzinfo is None
        ):
            raise SandboxFailure("SANDBOX_SCOPE_INVALID")
        seconds = math.ceil(
            (self.deadline - datetime.now(timezone.utc)).total_seconds()
        )
        if not 1 <= seconds <= 3600:
            raise SandboxFailure("RUN_DEADLINE_EXCEEDED")
        return seconds

    def metadata(self) -> dict[str, str]:
        return {
            "oa.platform": "open-agent",
            # A Kubernetes label holds at most 63 characters. Base32 preserves
            # the full SHA-256 digest in 52 characters; hex would be rejected.
            "oa.tenant": base64.b32encode(
                hashlib.sha256(self.tenant_id.encode()).digest()
            )
            .decode("ascii")
            .lower()
            .rstrip("="),
            "oa.environment": self.environment,
            "oa.run": str(self.run_id),
            "oa.attempt": str(self.attempt),
            "oa.lease": str(self.lease_id),
        }


class LeaseJournal(Protocol):
    async def created(self, scope: RunScope, sandbox_id: str) -> None: ...
    async def cleanup_pending(self, scope: RunScope, sandbox_id: str) -> None: ...
    async def released(self, scope: RunScope, sandbox_id: str) -> None: ...


class IsolationAdmission(Protocol):
    """Implementations inspect the configured runtime/network and reject stale or missing evidence."""

    async def before_create(
        self, scope: RunScope, profile: str, image: str
    ) -> None: ...
    async def after_create(
        self, scope: RunScope, profile: str, sandbox_id: str
    ) -> None: ...


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    truncated: bool


class AttemptSandbox:
    def __init__(
        self,
        scope: RunScope,
        sandbox: Sandbox,
        journal: LeaseJournal,
        permission: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.scope, self.sandbox, self.journal = scope, sandbox, journal
        self.permission = permission
        self._closed = False
        self._destroyed = False
        self._cleanup_complete = False
        self._close_lock = asyncio.Lock()
        self._execute_lock = asyncio.Lock()
        self._output_bytes = 0
        self._staged = False
        self._command_started = False
        self._skill_files: dict[str, bytes] = {}

    async def _check_permission(self) -> None:
        self.scope.remaining()
        if self.permission is not None:
            await self.permission()
        if self._closed:
            raise SandboxFailure("SANDBOX_UNAVAILABLE")

    def skill_file(self, path: str) -> bytes | None:
        if self._closed or not self._staged:
            raise SandboxFailure("SANDBOX_UNAVAILABLE")
        return self._skill_files.get(path)

    async def stage(self, releases: list[VerifiedRelease]) -> list[str]:
        if self._closed or self._staged or self._command_started:
            raise SandboxFailure("SANDBOX_ALREADY_STARTED")
        names = [release.manifest.name for release in releases]
        if len(set(names)) != len(names) or len(releases) > 20:
            raise SandboxFailure("SKILL_BINDING_INVALID")
        if (
            sum(
                sum(len(content) for content in release.contents)
                for release in releases
            )
            > 20 * 1024 * 1024
        ):
            raise SandboxFailure("SKILL_TOTAL_SIZE_EXCEEDED")
        roots: list[str] = []
        try:
            await self._check_permission()
            async with asyncio.timeout(min(60, self.scope.remaining())):
                await self.sandbox.files.create_directories(
                    [
                        WriteEntry(
                            path="/skills", mode=755, owner="root", group="root"
                        ),
                        WriteEntry(
                            path="/workspace", mode=700, owner="agent", group="agent"
                        ),
                        WriteEntry(
                            path="/workspace/tmp",
                            mode=700,
                            owner="agent",
                            group="agent",
                        ),
                        WriteEntry(
                            path="/opt/open-agent/transfers",
                            mode=711,
                            owner="root",
                            group="root",
                        ),
                    ]
                )
                for release in releases:
                    await self._check_permission()
                    if len(release.contents) != len(release.manifest.files):
                        raise SandboxFailure("SKILL_ARTIFACT_INVALID")
                    root = "/skills/" + release.manifest.name
                    roots.append(root)
                    directories = {root}
                    for file in release.manifest.files:
                        pieces = file.path.split("/")[:-1]
                        directories.update(
                            root + "/" + "/".join(pieces[:index])
                            for index in range(1, len(pieces) + 1)
                        )
                    await self.sandbox.files.create_directories(
                        [
                            WriteEntry(path=path, mode=755, owner="root", group="root")
                            for path in sorted(directories)
                        ]
                    )
                    for file, content in zip(
                        release.manifest.files, release.contents, strict=True
                    ):
                        if (
                            len(content) != file.size
                            or hashlib.sha256(content).hexdigest() != file.sha256
                        ):
                            raise SandboxFailure("SKILL_ARTIFACT_INVALID")
                        await self.sandbox.files.write_files(
                            [
                                WriteEntry(
                                    path=root + "/" + file.path,
                                    data=content,
                                    mode=444,
                                    owner="root",
                                    group="root",
                                )
                            ]
                        )
                self._staged = True
                self._skill_files = {
                    "/skills/" + release.manifest.name + "/" + file.path: content
                    for release in releases
                    for file, content in zip(
                        release.manifest.files, release.contents, strict=True
                    )
                }
            return roots
        except BaseException:
            await self.close()
            raise

    async def execute(self, command: str, timeout: int | None = None) -> CommandResult:
        if not self._staged or self._closed:
            raise SandboxFailure("SANDBOX_UNAVAILABLE")
        if not command.strip() or len(command.encode()) > 128 * 1024 or "\0" in command:
            raise SandboxFailure("SANDBOX_COMMAND_INVALID")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 0
        ):
            raise SandboxFailure("SANDBOX_COMMAND_INVALID")
        async with self._execute_lock:
            if self._closed:
                raise SandboxFailure("SANDBOX_UNAVAILABLE")
            await self._check_permission()
            self._command_started = True
            seconds = min(self.scope.remaining(), timeout or 120, 600)
            stdout = bytearray()
            stderr = bytearray()
            truncated = False

            async def capture(message: object, buffer: bytearray) -> None:
                nonlocal truncated
                # execd 1.1.0 emits lines without delimiters (blank lines are
                # emitted as "\n"). Preserve boundaries for native file tools.
                line = str(getattr(message, "text", ""))
                raw = (line if line.endswith("\n") else line + "\n").encode(
                    "utf-8", errors="replace"
                )
                remaining = max(
                    0,
                    min(
                        64 * 1024 - len(stdout) - len(stderr),
                        1024 * 1024 - self._output_bytes,
                    ),
                )
                buffer.extend(raw[:remaining])
                self._output_bytes += min(len(raw), remaining)
                truncated |= len(raw) > remaining

            async def out(message: object) -> None:
                await capture(message, stdout)

            async def err(message: object) -> None:
                await capture(message, stderr)

            # These hard process/file limits cannot be raised by the unprivileged child.
            limited = (
                "ulimit -S -u 128 && ulimit -H -u 128 && ulimit -S -f 65536 && ulimit -H -f 65536 && "
                "exec /usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/workspace TMPDIR=/workspace/tmp /bin/bash -c "
                + shlex.quote(command)
            )
            wrapped = "/bin/bash -c " + shlex.quote(limited)
            try:
                async with asyncio.timeout(seconds + 2):
                    result = await self.sandbox.commands.run(
                        wrapped,
                        opts=RunCommandOpts(
                            working_directory="/workspace",
                            timeout=timedelta(seconds=seconds),
                            uid=1000,
                            gid=1000,
                            # 1.1.0's ordinary command API inherits daemon env.
                            # Clear the token before the first UID-1000 process
                            # exists, then env -i removes all non-allowlisted vars.
                            envs={
                                "HOME": "/workspace",
                                "TMPDIR": "/workspace/tmp",
                                "EXECD_ACCESS_TOKEN": "",
                            },
                        ),
                        handlers=ExecutionHandlers(
                            on_stdout=out, on_stderr=err, skip_accumulation=True
                        ),
                    )
                if result.exit_code is None:
                    raise SandboxFailure("SANDBOX_PROTOCOL_ERROR")
                return CommandResult(
                    stdout.decode("utf-8", errors="replace"),
                    stderr.decode("utf-8", errors="replace"),
                    result.exit_code,
                    truncated,
                )
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception as exception:
                await self.close()
                if isinstance(exception, SandboxFailure):
                    raise
                if isinstance(exception, TimeoutError):
                    raise SandboxFailure("SANDBOX_TIMEOUT") from None
                raise SandboxFailure("SANDBOX_EXECUTION_FAILED") from None

    async def close(self) -> None:
        async with self._close_lock:
            if self._cleanup_complete:
                return
            self._closed = True
            self._skill_files.clear()
            # A failed kill stays pending for reconciliation; remote TTL remains a backstop.
            try:
                await self.journal.cleanup_pending(self.scope, self.sandbox.id)
            except Exception:
                pass  # A database outage must not prevent a remote kill attempt.
            if not self._destroyed:
                try:
                    async with asyncio.timeout(20):
                        await self.sandbox.destroy()
                    self._destroyed = True
                except Exception:
                    return
            try:
                await self.journal.released(self.scope, self.sandbox.id)
                self._cleanup_complete = True
            except Exception:
                pass  # Reconciliation will observe that the sandbox no longer exists.


class OpenSandboxProvider:
    def __init__(
        self,
        connection: ConnectionConfig,
        journal: LeaseJournal,
        admission: IsolationAdmission,
        *,
        profile: Literal["kata", "gvisor-demo"],
        cpu: str = "1",
        memory: str = "512Mi",
        factory: Callable[..., Awaitable[Sandbox]] = Sandbox.create,
        permission: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if (
            not connection.use_server_proxy
            or connection.debug
            or connection.transport is None
        ):
            raise SandboxFailure("SANDBOX_CONNECTION_UNSAFE")
        if profile not in {"kata", "gvisor-demo"}:
            raise SandboxFailure("SANDBOX_ISOLATION_UNAVAILABLE")
        self.connection, self.journal, self.admission = connection, journal, admission
        self.permission = permission
        self.profile, self.cpu, self.memory, self.factory = (
            profile,
            cpu,
            memory,
            factory,
        )

    async def create(
        self, scope: RunScope, releases: list[VerifiedRelease]
    ) -> AttemptSandbox:
        if (
            not releases
            or len({release.manifest.runtimeImage for release in releases}) != 1
        ):
            raise SandboxFailure("SKILL_RUNTIME_MISMATCH")
        if scope.environment == "prod" and self.profile != "kata":
            raise SandboxFailure("SANDBOX_ISOLATION_UNAVAILABLE")
        image = releases[0].manifest.runtimeImage
        seconds = scope.remaining()
        if self.permission is not None:
            await self.permission()
        await self.admission.before_create(scope, self.profile, image)
        if self.permission is not None:
            await self.permission()
        seconds = scope.remaining()
        # OpenSandbox 1.1.0 rejects its iptables egress sidecar with gVisor. Demo admission
        # must validate its dedicated Docker network/firewall instead; production uses Kata/CNI.
        policy = (
            NetworkPolicy(defaultAction="deny", egress=[])
            if self.profile == "kata"
            else None
        )
        connection = self.connection
        environment: dict[str, str] = {}
        if self.profile == "gvisor-demo":
            # Docker does not implement secureAccess in OpenSandbox 1.1.0. Its
            # execd supports a separate per-attempt token. execute() clears it
            # before launching child processes. Never substitute a management/model key.
            execd_token = secrets.token_urlsafe(48)
            environment["EXECD_ACCESS_TOKEN"] = execd_token
            connection = self.connection.model_copy(
                update={
                    "headers": {
                        **self.connection.headers,
                        "X-EXECD-ACCESS-TOKEN": execd_token,
                    }
                }
            )
        try:
            async with asyncio.timeout(min(90, seconds)):
                sandbox = await self.factory(
                    image,
                    timeout=timedelta(seconds=seconds),
                    ready_timeout=timedelta(seconds=min(60, seconds)),
                    connection_config=connection,
                    metadata=scope.metadata(),
                    resource={"cpu": self.cpu, "memory": self.memory},
                    env=environment,
                    volumes=[],
                    extensions={},
                    secure_access=self.profile == "kata",
                    network_policy=policy,
                    entrypoint=["/bin/bash", "-c", "exec sleep infinity"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not blindly retry creation after an ambiguous response. Reconcile by lease metadata.
            raise SandboxFailure("SANDBOX_CREATE_FAILED") from None
        lease = AttemptSandbox(scope, sandbox, self.journal, self.permission)
        try:
            await self.journal.created(scope, sandbox.id)
            await self.admission.after_create(scope, self.profile, sandbox.id)
            await lease.stage(releases)
            return lease
        except BaseException:
            await lease.close()
            raise

    async def find_attempt_sandboxes(self, scope: RunScope) -> list[str]:
        """Recovery uses trusted lease identity, never a sandbox ID supplied by a tenant."""
        manager = await SandboxManager.create(connection_config=self.connection)
        try:
            response = await manager.list_sandbox_infos(
                SandboxFilter(metadata=scope.metadata(), page_size=100, page=1)
            )
            if response.pagination.total_items > 100:
                raise SandboxFailure("SANDBOX_RECOVERY_LIMIT")
            return [
                item.id
                for item in response.sandbox_infos
                if all(
                    (item.metadata or {}).get(key) == value
                    for key, value in scope.metadata().items()
                )
            ]
        finally:
            await manager.close()
