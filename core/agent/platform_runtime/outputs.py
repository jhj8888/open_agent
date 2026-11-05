"""Bounded output collection, outside the model's text/tool output channel."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import shlex
from collections.abc import AsyncIterator
from datetime import timedelta
from importlib.resources import files
from deepagents.backends.utils import file_data_to_string

from opensandbox.models.execd import ExecutionHandlers, RunCommandOpts

from agent.platform_runtime.sandbox import AttemptSandbox, SandboxFailure

MAX_FILE_BYTES = 32 * 1024 * 1024
OUTPUT_ROOT = "/workspace/outputs/"


async def snapshot(attempt: AttemptSandbox) -> list[dict]:
    await attempt._check_permission()
    token = secrets.token_hex(16)
    script = (
        files("agent.platform_runtime")
        .joinpath("output_snapshot.py")
        .read_text(encoding="utf-8")
    )
    command = (
        "/usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin python3 -I -c "
        + shlex.quote(script)
        + " "
        + token
    )
    output = bytearray()

    async def capture(message):
        data = str(getattr(message, "text", "")).encode()
        if len(output) + len(data) > 256 * 1024:
            raise SandboxFailure("OUTPUT_MANIFEST_LIMIT")
        output.extend(data)

    async with attempt._execute_lock, asyncio.timeout(
        min(60, attempt.scope.remaining())
    ):
        result = await attempt.sandbox.commands.run(
            command,
            opts=RunCommandOpts(
                uid=0,
                gid=0,
                working_directory="/",
                timeout=timedelta(seconds=55),
                envs={"EXECD_ACCESS_TOKEN": ""},
            ),
            handlers=ExecutionHandlers(on_stdout=capture, skip_accumulation=True),
        )
    if result.exit_code != 0:
        raise SandboxFailure("OUTPUT_SNAPSHOT_REJECTED")
    try:
        rows = json.loads(output)["files"]
        if not isinstance(rows, list) or len(rows) > 32:
            raise ValueError()
        for index, row in enumerate(rows):
            if (
                row["snapshot"] != f"/opt/open-agent/outputs/{token}/{index}"
                or type(row["size"]) is not int
                or not 0 <= row["size"] <= MAX_FILE_BYTES
            ):
                raise ValueError()
        if sum(row["size"] for row in rows) > 128 * 1024 * 1024:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise SandboxFailure("OUTPUT_MANIFEST_INVALID") from None
    await attempt._check_permission()
    return rows


async def read_snapshot(attempt: AttemptSandbox, row: dict) -> AsyncIterator[bytes]:
    """Official range downloads stay below the shared SDK transport's 8 MiB cap."""
    digest = hashlib.sha256()
    size = row["size"]
    for offset in range(0, size, 1024 * 1024):
        await attempt._check_permission()
        end = min(size, offset + 1024 * 1024)
        # SDK read_bytes is bounded by both the requested range and transport.
        content = await attempt.sandbox.files.read_bytes(
            row["snapshot"], range_header=f"bytes={offset}-{end-1}"
        )
        if len(content) != end - offset:
            raise SandboxFailure("OUTPUT_SIZE_MISMATCH")
        digest.update(content)
        yield content
    if digest.hexdigest() != row["sha256"]:
        raise SandboxFailure("OUTPUT_DIGEST_MISMATCH")


def state_bytes(value: dict) -> bytes:
    # Official converter supports current strings and legacy lists of lines.
    content = value.get("content", "")
    if not isinstance(content, (str, list)) or (
        isinstance(content, list) and not all(isinstance(line, str) for line in content)
    ):
        raise SandboxFailure("OUTPUT_STATE_INVALID")
    length = (
        len(content)
        if isinstance(content, str)
        else sum(len(line) for line in content) + len(content)
    )
    if length > MAX_FILE_BYTES * 2:
        raise SandboxFailure("OUTPUT_SIZE_LIMIT")
    text = file_data_to_string(value)
    encoding = value.get("encoding", "utf-8")
    if encoding not in {"utf-8", "base64"}:
        raise SandboxFailure("OUTPUT_STATE_INVALID")
    result = (
        text.encode() if encoding == "utf-8" else base64.b64decode(text, validate=True)
    )
    if len(result) > MAX_FILE_BYTES:
        raise SandboxFailure("OUTPUT_SIZE_LIMIT")
    return result
