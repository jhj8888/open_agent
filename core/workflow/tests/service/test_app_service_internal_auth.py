"""Tenant management calls must include the deployment-managed internal key."""

from unittest.mock import patch

import pytest

from workflow.service.app_service import _gen_app_auth_header
from workflow.utils.credentials import TENANT_INTERNAL_API_KEY_HEADER


def test_gen_app_auth_header_includes_tenant_internal_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_MANAGE_PLAT_KEY", "k" * 48)
    monkeypatch.setenv("APP_MANAGE_PLAT_SECRET", "s" * 48)
    monkeypatch.delenv("APP_MANAGE_PLAT_KEY_FILE", raising=False)
    monkeypatch.delenv("APP_MANAGE_PLAT_SECRET_FILE", raising=False)

    with patch(
        "workflow.service.app_service.HMACAuth.build_auth_header",
        return_value={"Authorization": "signed"},
    ) as build_auth_header:
        headers = _gen_app_auth_header("http://core-tenant:5052/v2/app/details")

    assert headers == {
        "Authorization": "signed",
        TENANT_INTERNAL_API_KEY_HEADER: "s" * 48,
    }
    build_auth_header.assert_called_once_with(
        request_url="http://core-tenant:5052/v2/app/details",
        api_key="k" * 48,
        api_secret="s" * 48,
    )


def test_gen_app_auth_header_fails_closed_without_complete_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_MANAGE_PLAT_KEY", "k" * 48)
    monkeypatch.delenv("APP_MANAGE_PLAT_SECRET", raising=False)
    monkeypatch.delenv("APP_MANAGE_PLAT_KEY_FILE", raising=False)
    monkeypatch.delenv("APP_MANAGE_PLAT_SECRET_FILE", raising=False)

    assert _gen_app_auth_header("http://core-tenant:5052/v2/app/list") == {}
