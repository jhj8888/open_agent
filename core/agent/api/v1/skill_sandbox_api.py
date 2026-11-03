"""HTTP endpoint that runs a single skill command in the E2B sandbox.

Reuses the audited ``E2BSandboxProvider`` so the Java standalone-agent runtime
(which has no E2B SDK) can execute ``run_skill`` via one internal HTTP call.
v1 returns only exit_code/stdout/stderr; artifact collection/upload is skipped.
"""

import hashlib
import hmac
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agent.service.plugin.skill import SkillResource
from agent.service.plugin.skill_sandbox import (
    SCRIPT_SANDBOX_UNCONFIGURED_MESSAGE,
    E2BSandboxProvider,
    SandboxExecutionRequest,
    SkillSandboxConfig,
    _load_runtime_credential_token,
)

skill_sandbox_router = APIRouter()
EXECUTION_TIMESTAMP_HEADER = "X-Skill-Sandbox-Execution-Timestamp"
EXECUTION_SIGNATURE_HEADER = "X-Skill-Sandbox-Execution-Signature"
EXECUTION_SIGNATURE_MAX_AGE_SECONDS = 300


class SandboxExecBody(BaseModel):
    skill_id: str
    command: str
    stdin: Any = None
    resources: list[dict[str, Any]] = Field(default_factory=list)
    sandbox: dict[str, Any] = Field(default_factory=dict)


class SandboxExecResponse(BaseModel):
    configured: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    message: str = ""


def _build_config(raw: dict[str, Any]) -> SkillSandboxConfig:
    return SkillSandboxConfig(
        enabled=bool(raw.get("enabled")),
        workflow_id=str(
            raw.get("workflow_id") or raw.get("workflowId") or raw.get("flowId") or ""
        ),
        uid=str(raw.get("uid") or ""),
        space_id=str(raw.get("space_id") or raw.get("spaceId") or ""),
    )


def _verify_execution_signature(
    raw_body: bytes,
    timestamp_value: str | None,
    signature_value: str | None,
    *,
    now_seconds: int | None = None,
) -> None:
    try:
        if (
            timestamp_value is None
            or not timestamp_value.isascii()
            or not timestamp_value.isdigit()
            or not 1 <= len(timestamp_value) <= 20
            or signature_value is None
            or len(signature_value) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in signature_value)
        ):
            raise ValueError
        timestamp = int(timestamp_value)
        now = int(time.time()) if now_seconds is None else now_seconds
        if abs(now - timestamp) > EXECUTION_SIGNATURE_MAX_AGE_SECONDS:
            raise ValueError
        token = _load_runtime_credential_token()
        canonical = timestamp_value.encode("ascii") + b"\n" + raw_body
        expected = hmac.new(
            token.encode("utf-8"), canonical, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature_value.lower()):
            raise ValueError
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized") from None


@skill_sandbox_router.post(  # type: ignore[misc]
    "/skill/sandbox-exec",
    description="Execute a single skill command in the E2B sandbox (no artifact handling).",
    response_model=SandboxExecResponse,
)
async def sandbox_exec(body: SandboxExecBody, request: Request) -> SandboxExecResponse:
    _verify_execution_signature(
        await request.body(),
        request.headers.get(EXECUTION_TIMESTAMP_HEADER),
        request.headers.get(EXECUTION_SIGNATURE_HEADER),
    )
    config = _build_config(body.sandbox)
    configured = config.enabled
    if not configured:
        return SandboxExecResponse(
            configured=False, message=SCRIPT_SANDBOX_UNCONFIGURED_MESSAGE
        )
    if not body.command.strip():
        return SandboxExecResponse(
            configured=True, exit_code=1, stderr="command_required"
        )

    resources = [
        SkillResource(
            path=str(r.get("path") or ""),
            name=str(r.get("name") or ""),
            download_url=str(r.get("download_url") or r.get("downloadUrl") or ""),
            file_ext=str(r.get("file_ext") or r.get("fileExt") or ""),
            file_size=int(r.get("file_size") or r.get("fileSize") or 0),
        )
        for r in body.resources
    ]
    request = SandboxExecutionRequest(
        skill_id=body.skill_id,
        command=body.command,
        stdin=body.stdin,
        resources=resources,
    )
    result = await E2BSandboxProvider(config).execute(request)
    return SandboxExecResponse(
        configured=True,
        exit_code=int(result.get("exit_code") or 0),
        stdout=str(result.get("stdout") or ""),
        stderr=str(result.get("stderr") or ""),
    )
