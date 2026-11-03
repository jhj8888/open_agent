"""Security regression tests for the direct Skill sandbox endpoint."""

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from agent.api.v1 import skill_sandbox_api
from agent.api.v1.skill_sandbox_api import (
    EXECUTION_SIGNATURE_HEADER,
    EXECUTION_TIMESTAMP_HEADER,
    SandboxExecBody,
    _build_config,
    _verify_execution_signature,
)
from agent.service.plugin.skill_sandbox import (
    RUNTIME_CONFIG_URL_ENV,
    RUNTIME_CREDENTIAL_TOKEN_ENV,
    RUNTIME_CREDENTIAL_TOKEN_FILE_ENV,
    SANDBOX_RUNTIME_CONFIG_ERROR,
)


def _signed_request(
    raw_body: bytes, token: str, timestamp: int = 1_700_000_000
) -> Request:
    timestamp_value = str(timestamp)
    signature = hmac.new(
        token.encode("utf-8"),
        timestamp_value.encode("ascii") + b"\n" + raw_body,
        hashlib.sha256,
    ).hexdigest()
    headers = [
        (EXECUTION_TIMESTAMP_HEADER.lower().encode("ascii"), timestamp_value.encode()),
        (EXECUTION_SIGNATURE_HEADER.lower().encode("ascii"), signature.encode()),
    ]
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": raw_body, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": headers},
        receive,
    )


def test_direct_sandbox_config_ignores_untrusted_credentials_and_url() -> None:
    config = _build_config(
        {
            "provider": "e2b",
            "enabled": True,
            "apiKey": "legacy-api-key-must-not-be-used",
            "api_key": "legacy-snake-api-key-must-not-be-used",
            "runtimeConfigUrl": "https://attacker.example/runtime-config",
            "runtime_config_url": "https://attacker.example/snake-runtime-config",
            "artifactUploadToken": "payload-artifact-token-must-not-be-used",
            "runtimeCredentialToken": "payload-runtime-token-must-not-be-used",
            "flowId": "flow-1",
            "uid": "user-1",
            "spaceId": "space-1",
        }
    )

    dumped = config.model_dump()
    assert config.workflow_id == "flow-1"
    assert config.uid == "user-1"
    assert config.space_id == "space-1"
    assert "provider" not in dumped
    assert "timeout_seconds" not in dumped
    assert "allow_internet_access" not in dumped
    assert "artifact_upload_url" not in dumped
    assert "api_key" not in dumped
    assert "runtime_config_url" not in dumped
    assert "artifact_upload_token" not in dumped
    assert "runtime_credential_token" not in dumped


@pytest.mark.asyncio
async def test_direct_sandbox_endpoint_delegates_without_payload_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_config: dict[str, Any] = {}

    class FakeProvider:
        def __init__(self, config: Any) -> None:
            observed_config.update(config.model_dump())

        async def execute(self, request: Any) -> dict[str, Any]:
            assert request.skill_id == "skill-1"
            assert request.command == "python scripts/run.py"
            return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(skill_sandbox_api, "E2BSandboxProvider", FakeProvider)
    token = "direct-execution-runtime-token-value-0001"
    timestamp = 1_700_000_000
    raw_body = json.dumps(
        {
            "skill_id": "skill-1",
            "command": "python scripts/run.py",
            "sandbox": {
                "provider": "e2b",
                "enabled": True,
                "apiKey": "legacy-api-key-must-not-be-used",
                "runtimeConfigUrl": "https://attacker.example/runtime-config",
                "workflowId": "flow-1",
            },
        },
        separators=(",", ":"),
    ).encode()
    body = SandboxExecBody.model_validate_json(raw_body)
    monkeypatch.setenv(RUNTIME_CREDENTIAL_TOKEN_ENV, token)
    monkeypatch.setattr(skill_sandbox_api.time, "time", lambda: timestamp)

    response = await skill_sandbox_api.sandbox_exec(
        body, _signed_request(raw_body, token, timestamp)
    )

    assert response.configured is True
    assert response.stdout == "ok"
    assert observed_config["workflow_id"] == "flow-1"
    assert "api_key" not in observed_config
    assert "runtime_config_url" not in observed_config


@pytest.mark.parametrize("case", ["missing", "stale", "bad", "tampered"])
def test_direct_sandbox_execution_requires_fresh_body_hmac(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    token = "direct-execution-runtime-token-value-0001"
    now = 1_700_000_000
    raw_body = b'{"skill_id":"skill-1"}'
    timestamp = now if case != "stale" else now - 301
    timestamp_value = str(timestamp)
    signature: str | None = hmac.new(
        token.encode(),
        timestamp_value.encode() + b"\n" + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if case == "missing":
        signature = None
    elif case == "bad":
        signature = "0" * 64
    elif case == "tampered":
        raw_body += b" "
    monkeypatch.setenv(RUNTIME_CREDENTIAL_TOKEN_ENV, token)

    with pytest.raises(HTTPException) as exc_info:
        _verify_execution_signature(
            raw_body,
            timestamp_value,
            signature,
            now_seconds=now,
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Unauthorized"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_setting", ["url", "token"])
async def test_direct_enabled_e2b_missing_deployment_config_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    missing_setting: str,
) -> None:
    monkeypatch.setattr(
        skill_sandbox_api, "_verify_execution_signature", lambda *a: None
    )
    monkeypatch.delenv(RUNTIME_CREDENTIAL_TOKEN_FILE_ENV, raising=False)
    if missing_setting == "url":
        monkeypatch.delenv(RUNTIME_CONFIG_URL_ENV, raising=False)
        monkeypatch.setenv(
            RUNTIME_CREDENTIAL_TOKEN_ENV,
            "agent-runtime-credential-token-value-0001",
        )
    else:
        monkeypatch.setenv(
            RUNTIME_CONFIG_URL_ENV,
            "http://console-hub:8080/skill-sandbox/internal-runtime-config",
        )
        monkeypatch.delenv(RUNTIME_CREDENTIAL_TOKEN_ENV, raising=False)
    body = SandboxExecBody(
        skill_id="skill-1",
        command="python scripts/run.py",
        sandbox={"provider": "e2b", "enabled": True, "uid": "user-1"},
    )

    with pytest.raises(RuntimeError) as exc_info:
        await skill_sandbox_api.sandbox_exec(
            body,
            _signed_request(b"{}", "unused-runtime-token-value-0000001"),
        )

    assert str(exc_info.value) == SANDBOX_RUNTIME_CONFIG_ERROR
