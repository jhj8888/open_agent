from pathlib import Path

import pytest
from fastapi import HTTPException

from agent.api.v1.workflow_agent import workflow_agent_router
from agent.infra.workflow_internal_auth import (
    WORKFLOW_INTERNAL_API_KEY_ENV,
    WORKFLOW_INTERNAL_API_KEY_FILE_ENV,
    WORKFLOW_INTERNAL_API_KEY_PLACEHOLDER,
    require_workflow_internal_api_key,
)


@pytest.mark.asyncio
async def test_agent_internal_auth_accepts_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_key = "a" * 32
    monkeypatch.setenv(WORKFLOW_INTERNAL_API_KEY_ENV, configured_key)
    monkeypatch.delenv(WORKFLOW_INTERNAL_API_KEY_FILE_ENV, raising=False)

    await require_workflow_internal_api_key(configured_key)


@pytest.mark.asyncio
async def test_agent_internal_auth_accepts_persisted_key_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured_key = "f" * 48
    key_file = tmp_path / "workflow-internal-api-key"
    key_file.write_text(f"{configured_key}\n", encoding="utf-8")
    monkeypatch.setenv(
        WORKFLOW_INTERNAL_API_KEY_ENV, WORKFLOW_INTERNAL_API_KEY_PLACEHOLDER
    )
    monkeypatch.setenv(WORKFLOW_INTERNAL_API_KEY_FILE_ENV, str(key_file))

    await require_workflow_internal_api_key(configured_key)


@pytest.mark.asyncio
async def test_agent_internal_auth_rejects_missing_or_wrong_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_key = "a" * 32
    monkeypatch.setenv(WORKFLOW_INTERNAL_API_KEY_ENV, configured_key)
    monkeypatch.delenv(WORKFLOW_INTERNAL_API_KEY_FILE_ENV, raising=False)

    for supplied_key in (None, "", "wrong-secret"):
        with pytest.raises(HTTPException) as exc_info:
            await require_workflow_internal_api_key(supplied_key)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_key", ["", WORKFLOW_INTERNAL_API_KEY_PLACEHOLDER])
async def test_agent_internal_auth_fails_closed_when_not_configured(
    monkeypatch: pytest.MonkeyPatch, configured_key: str
) -> None:
    monkeypatch.setenv(WORKFLOW_INTERNAL_API_KEY_ENV, configured_key)
    monkeypatch.delenv(WORKFLOW_INTERNAL_API_KEY_FILE_ENV, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        await require_workflow_internal_api_key(configured_key)

    assert exc_info.value.status_code == 503


def test_every_workflow_agent_route_requires_internal_auth() -> None:
    for route in workflow_agent_router.routes:
        assert any(
            dependency.call is require_workflow_internal_api_key
            for dependency in route.dependant.dependencies
        ), route.path
