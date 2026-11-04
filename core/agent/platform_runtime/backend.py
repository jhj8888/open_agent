"""DeepAgents' official BaseSandbox maps native filesystem/Skill tools to this attempt.

Root SDK writes are restricted to generated transfer paths. Model-directed filesystem
operations always run as the unprivileged sandbox user, including uploads.
"""

from __future__ import annotations

import asyncio
import base64
import posixpath
import shlex
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar
from uuid import uuid4

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox.models.filesystem import WriteEntry

from agent.platform_runtime.artifacts import MAX_FILE_BYTES, MAX_TOTAL_BYTES, safe_path
from agent.platform_runtime.sandbox import AttemptSandbox, SandboxFailure

T = TypeVar("T")

# Runs inside the sandbox as UID 1000. openat + O_NOFOLLOW protects each path
# component, and a bounded read protects against a file growing after fstat.
_SMALL_DOWNLOAD = """
import base64, os, stat, sys
fd = os.open('/workspace', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    parts = sys.argv[1].split('/')
    for part in parts[:-1]:
        next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        fd = next_fd
    source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        info = os.fstat(source)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
            raise ValueError('file_too_large_or_not_regular')
        data = bytearray()
        while len(data) <= 32768:
            chunk = os.read(source, 32769 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > 32768:
            raise ValueError('file_too_large')
        print(base64.b64encode(data).decode('ascii'))
    finally:
        os.close(source)
finally:
    os.close(fd)
""".strip()


class OpenSandboxBackend(BaseSandbox):
    def __init__(self, lease: AttemptSandbox) -> None:
        self.lease = lease
        self.loop = asyncio.get_running_loop()

    @property
    def id(self) -> str:
        return self.lease.sandbox.id

    def _sync(self, operation: Callable[[], Coroutine[Any, Any, T]]) -> T:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is self.loop:
            raise RuntimeError("Use the async backend methods on the worker event loop")
        future = asyncio.run_coroutine_threadsafe(operation(), self.loop)
        try:
            return future.result(timeout=self.lease.scope.remaining() + 25)
        except TimeoutError:
            future.cancel()
            raise SandboxFailure("RUN_DEADLINE_EXCEEDED") from None

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self._sync(lambda: self.aexecute(command, timeout=timeout))

    async def aexecute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        result = await self.lease.execute(command, timeout)
        output = result.stdout + (
            "\n[stderr]\n" + result.stderr if result.stderr else ""
        )
        return ExecuteResponse(
            output=output, exit_code=result.exit_code, truncated=result.truncated
        )

    @staticmethod
    def _workspace_path(path: str) -> str:
        if not path.startswith("/workspace/"):
            raise ValueError("Writes and artifact downloads must stay in /workspace")
        return safe_path(path[len("/workspace/") :])

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._sync(lambda: self.aupload_files(files))

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        if (
            len(files) > 100
            or sum(len(content) for _, content in files) > MAX_TOTAL_BYTES
        ):
            raise SandboxFailure("SKILL_TOTAL_SIZE_EXCEEDED")
        results = []
        for path, content in files:
            try:
                self._workspace_path(path)
            except (ValueError, RuntimeError):
                results.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            if len(content) > MAX_FILE_BYTES:
                results.append(FileUploadResponse(path=path, error="file_too_large"))
                continue
            transfer = "/opt/open-agent/transfers/" + uuid4().hex
            try:
                await self.lease.sandbox.files.write_files(
                    [
                        WriteEntry(
                            path=transfer,
                            data=content,
                            mode=444,
                            owner="root",
                            group="root",
                        )
                    ]
                )
                # cp runs as UID 1000. A malicious destination symlink cannot grant root write access.
                result = await self.lease.execute(
                    "mkdir -p -- "
                    + shlex.quote(posixpath.dirname(path))
                    + " && cp -- "
                    + shlex.quote(transfer)
                    + " "
                    + shlex.quote(path),
                    timeout=30,
                )
                results.append(
                    FileUploadResponse(
                        path=path,
                        error=None if result.exit_code == 0 else "permission_denied",
                    )
                )
            finally:
                try:
                    await self.lease.sandbox.files.delete_files([transfer])
                except Exception:
                    pass  # The whole sandbox still has a finite TTL and is destroyed at attempt end.
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._sync(lambda: self.adownload_files(paths))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        if len(paths) > 20:
            raise SandboxFailure("ARTIFACT_COUNT_EXCEEDED")
        results = []
        for path in paths:
            if path.startswith("/skills/"):
                # SkillsMiddleware uses download_files to discover SKILL.md.
                # Serve only exact already-verified release bytes, never arbitrary
                # root SDK reads or user-modified files from the sandbox.
                content = self.lease.skill_file(path)
                results.append(
                    FileDownloadResponse(
                        path=path,
                        content=content,
                        error=None if content is not None else "file_not_found",
                    )
                )
                continue
            try:
                relative = self._workspace_path(path)
            except (ValueError, RuntimeError):
                results.append(FileDownloadResponse(path=path, error="invalid_path"))
                continue
            result = await self.lease.execute(
                "python3 -c "
                + shlex.quote(_SMALL_DOWNLOAD)
                + " "
                + shlex.quote(relative),
                timeout=30,
            )
            if result.exit_code != 0 or result.truncated:
                results.append(
                    FileDownloadResponse(
                        path=path, error="file_unavailable_or_exceeds_32_KiB"
                    )
                )
                continue
            try:
                content = base64.b64decode(result.stdout.strip(), validate=True)
                if len(content) > 32768:
                    raise ValueError()
                results.append(FileDownloadResponse(path=path, content=content))
            except ValueError:
                results.append(FileDownloadResponse(path=path, error="invalid_content"))
        return results
