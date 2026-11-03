from pathlib import Path

import pytest
from fastapi import APIRouter, HTTPException

from workflow.api.v1.chat.debug import router as chat_debug_router
from workflow.api.v1.chat.node_debug import router as node_debug_router
from workflow.api.v1.flow.auth import router as auth_router
from workflow.api.v1.flow.file import router as file_router
from workflow.api.v1.flow.layout import router as layout_router
from workflow.extensions.fastapi.base import AUTH_OPEN_API_PATHS
from workflow.extensions.fastapi.middleware.auth import (
    WORKFLOW_INTERNAL_API_KEY_ENV,
    WORKFLOW_INTERNAL_API_KEY_FILE_ENV,
    WORKFLOW_INTERNAL_API_KEY_MAX_FILE_BYTES,
    WORKFLOW_INTERNAL_API_KEY_PLACEHOLDER,
    require_workflow_internal_api_key,
)


@pytest.mark.asyncio
async def test_internal_api_auth_accepts_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_key = "s" * 32
    monkeypatch.setenv(WORKFLOW_INTERNAL_API_KEY_ENV, configured_key)
    monkeypatch.delenv(WORKFLOW_INTERNAL_API_KEY_FILE_ENV, raising=False)

    await require_workflow_internal_api_key(configured_key)


@pytest.mark.asyncio
async def test_internal_api_auth_rejects_missing_or_wrong_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_key = "s" * 32
    monkeypatch.setenv(WORKFLOW_INTERNAL_API_KEY_ENV, configured_key)
    monkeypatch.delenv(WORKFLOW_INTERNAL_API_KEY_FILE_ENV, raising=False)

    for supplied_key in (None, "", "wrong-secret"):
        with pytest.raises(HTTPException) as exc_info:
            await require_workflow_internal_api_key(supplied_key)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_key", ["", WORKFLOW_INTERNAL_API_KEY_PLACEHOLDER])
async def test_internal_api_auth_fails_closed_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
    configured_key: str,
) -> None:
    monkeypatch.setenv(WORKFLOW_INTERNAL_API_KEY_ENV, configured_key)

    with pytest.raises(HTTPException) as exc_info:
        await require_workflow_internal_api_key(configured_key)

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_internal_api_auth_reads_persisted_key_file(
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
@pytest.mark.parametrize("file_state", ["missing", "short", "oversized", "symlink"])
async def test_internal_api_auth_rejects_invalid_key_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, file_state: str
) -> None:
    key_file = tmp_path / "workflow-internal-api-key"
    if file_state == "short":
        key_file.write_text("too-short", encoding="utf-8")
    elif file_state == "oversized":
        key_file.write_text(
            "x" * (WORKFLOW_INTERNAL_API_KEY_MAX_FILE_BYTES + 1), encoding="utf-8"
        )
    elif file_state == "symlink":
        target = tmp_path / "target"
        target.write_text("f" * 48, encoding="utf-8")
        key_file.symlink_to(target)

    monkeypatch.delenv(WORKFLOW_INTERNAL_API_KEY_ENV, raising=False)
    monkeypatch.setenv(WORKFLOW_INTERNAL_API_KEY_FILE_ENV, str(key_file))

    with pytest.raises(HTTPException) as exc_info:
        await require_workflow_internal_api_key("f" * 48)

    assert exc_info.value.status_code == 503


def test_every_node_debug_route_requires_internal_auth() -> None:
    for route in node_debug_router.routes:
        assert any(
            dependency.call is require_workflow_internal_api_key
            for dependency in route.dependant.dependencies
        ), route.path


def test_every_chat_debug_route_requires_internal_auth() -> None:
    for route in chat_debug_router.routes:
        assert any(
            dependency.call is require_workflow_internal_api_key
            for dependency in route.dependant.dependencies
        ), route.path


@pytest.mark.parametrize("protected_router", [layout_router, file_router])
def test_every_workflow_management_route_requires_internal_auth(
    protected_router: APIRouter,
) -> None:
    for route in protected_router.routes:
        assert any(
            dependency.call is require_workflow_internal_api_key
            for dependency in route.dependant.dependencies
        ), route.path


def test_publish_and_binding_routes_remain_middleware_authenticated() -> None:
    mounted_paths = {
        f"{prefix}{route.path}"
        for prefix in ("/workflow/v1", "/v1")
        for route in auth_router.routes
    }
    assert mounted_paths == set(AUTH_OPEN_API_PATHS)
