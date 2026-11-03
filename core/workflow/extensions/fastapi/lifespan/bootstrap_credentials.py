"""Synchronize the deployment-managed tenant identity into Workflow storage."""

import os
from dataclasses import dataclass
from datetime import datetime

from loguru import logger
from sqlmodel import Session, select  # type: ignore

from workflow.domain.models.ai_app import App
from workflow.domain.models.app_source import AppSource
from workflow.extensions.middleware.database.utils import session_getter
from workflow.extensions.middleware.getters import get_cache_service
from workflow.utils.credentials import credential_cache_key, credential_from_env_or_file

BOOTSTRAP_TENANT_ID = "680ab54f"
LEGACY_TENANT_KEY = "7b709739e8da44536127a333c7603a83"
LEGACY_TENANT_SECRET = "NjhmY2NmM2NkZDE4MDFlNmM5ZjcyZjMy"
# SHA-256 of the published legacy pair.  Keep this non-secret fingerprint so
# upgrades can remove the exact v1 key even if a Redis deployment does not
# return it from the broad namespace scan below.
_LEGACY_VERIFIED_CACHE_DIGEST = (
    "8f36ac6b8a917de90c78ca6828908790c0f0df33a319e3b793a35e2dc988f18f"
)
_LEGACY_VERIFIED_CACHE_PREFIX = "workflow:app:verified_credential"
_PREVIOUS_VERIFIED_CACHE_PREFIX = "workflow:app:verified_credential:v2"
_CURRENT_VERIFIED_CACHE_PREFIX = "workflow:app:verified_credential:v3"
_LEGACY_VERIFIED_CACHE_PATTERN = f"{_LEGACY_VERIFIED_CACHE_PREFIX}:[0-9a-f]*"
_PREVIOUS_VERIFIED_CACHE_PATTERN = f"{_PREVIOUS_VERIFIED_CACHE_PREFIX}:*"
_OLDEST_API_KEY_CACHE_PREFIX = "workflow:app:api_key"
_LEGACY_APP_INFO_CACHE_PREFIX = "workflow:app_info"
_CURRENT_APP_INFO_CACHE_PREFIX = "workflow:app_info:v2"
_LEGACY_FLOW_CACHE_PATTERN = "workflow:flow_info:v2:*"


@dataclass(frozen=True)
class TenantBootstrapCredentials:
    tenant_id: str
    api_key: str
    api_secret: str


def load_tenant_bootstrap_credentials() -> TenantBootstrapCredentials:
    tenant_id = os.getenv("TENANT_ID", BOOTSTRAP_TENANT_ID).strip()
    api_key = credential_from_env_or_file(
        "TENANT_KEY", "TENANT_KEY_FILE", min_length=32, max_length=50
    )
    api_secret = credential_from_env_or_file(
        "TENANT_SECRET", "TENANT_SECRET_FILE", min_length=32, max_length=50
    )
    if tenant_id != BOOTSTRAP_TENANT_ID:
        raise RuntimeError(
            "TENANT_ID must remain 680ab54f because persisted bootstrap data refers to it"
        )
    if not api_key or not api_secret or api_key == api_secret:
        raise RuntimeError("Tenant bootstrap credentials are missing or invalid")
    if api_key == LEGACY_TENANT_KEY or api_secret == LEGACY_TENANT_SECRET:
        raise RuntimeError("Published legacy tenant credentials cannot be used")
    if not all(_is_safe_credential_character(character) for character in api_key):
        raise RuntimeError(
            "TENANT_KEY contains characters that are unsafe in authentication headers"
        )
    if not all(_is_safe_credential_character(character) for character in api_secret):
        raise RuntimeError(
            "TENANT_SECRET contains characters that are unsafe in authentication headers"
        )
    return TenantBootstrapCredentials(tenant_id, api_key, api_secret)


def _is_safe_credential_character(character: str) -> bool:
    return character.isascii() and (character.isalnum() or character in "._~-")


def synchronize_bootstrap_app(
    session: Session,
    credentials: TenantBootstrapCredentials,
    credential_digests_to_revoke: set[str] | None = None,
) -> None:
    """Update only the reserved bootstrap row; never touch user-created apps."""
    # app_source id=1 is immutable bootstrap seed data and provides an existing
    # InnoDB row that can serialize first-start reconciliation even while app id=1
    # does not exist yet. This avoids concurrent replicas racing the unique app keys.
    bootstrap_source = session.exec(
        select(AppSource).where(AppSource.id == 1).with_for_update()
    ).first()
    if bootstrap_source is None:
        raise RuntimeError("Reserved Workflow bootstrap source row is missing")

    bootstrap_app = session.exec(
        select(App).where(App.id == 1).with_for_update()
    ).first()
    alias_owner = session.exec(
        select(App).where(App.alias_id == credentials.tenant_id)
    ).first()

    if bootstrap_app is not None and bootstrap_app.alias_id != credentials.tenant_id:
        raise RuntimeError("Reserved Workflow bootstrap app id is owned by another app")
    if alias_owner is not None and alias_owner.id != 1:
        raise RuntimeError("Reserved Workflow bootstrap tenant alias is already in use")

    now = datetime.now()
    if bootstrap_app is None:
        bootstrap_app = App(
            id=1,
            name="星辰",
            alias_id=credentials.tenant_id,
            description="星辰",
            is_tenant=1,
            source=1,
            actual_source=1,
            plat_release_auth=1,
            status=1,
            audit_policy=0,
            create_by=1,
            update_by=1,
            create_at=now,
            update_at=now,
        )

    expected_identity = {
        "is_tenant": 1,
        "source": 1,
        "actual_source": 1,
        "plat_release_auth": 1,
        "status": 1,
    }
    mismatched_fields = [
        field
        for field, expected_value in expected_identity.items()
        if getattr(bootstrap_app, field) != expected_value
    ]
    if mismatched_fields:
        raise RuntimeError(
            "Reserved Workflow bootstrap app identity is invalid: "
            + ", ".join(mismatched_fields)
        )

    desired_pair = (credentials.api_key, credentials.api_secret)
    existing_pair = (bootstrap_app.api_key, bootstrap_app.api_secret)
    if (
        credential_digests_to_revoke is not None
        and existing_pair != desired_pair
        and all(existing_pair)
    ):
        credential_digests_to_revoke.add(
            credential_cache_key(f"{existing_pair[0]}:{existing_pair[1]}")
        )
    # This row is deployment-owned once all reserved identity fields match.
    # Always converge it to the Secret so explicit rotations and regenerated
    # credential volumes recover without manual database edits.
    bootstrap_app.api_key, bootstrap_app.api_secret = desired_pair
    bootstrap_app.update_at = now
    session.add(bootstrap_app)


def invalidate_legacy_bootstrap_caches(
    credentials: TenantBootstrapCredentials,
    credential_digests_to_revoke: set[str] | None = None,
) -> None:
    """Revoke disclosed authentication entries and abandon secret-bearing flow caches."""
    cache_service = get_cache_service()
    legacy_digest = credential_cache_key(f"{LEGACY_TENANT_KEY}:{LEGACY_TENANT_SECRET}")
    keys = {
        f"{_OLDEST_API_KEY_CACHE_PREFIX}:{LEGACY_TENANT_KEY}",
        f"{_LEGACY_VERIFIED_CACHE_PREFIX}:{_LEGACY_VERIFIED_CACHE_DIGEST}",
        f"{_CURRENT_VERIFIED_CACHE_PREFIX}:{legacy_digest}",
        f"{_LEGACY_APP_INFO_CACHE_PREFIX}:{credentials.tenant_id}",
        f"{_CURRENT_APP_INFO_CACHE_PREFIX}:{credentials.tenant_id}",
    }
    keys.update(
        f"{_CURRENT_VERIFIED_CACHE_PREFIX}:{digest}"
        for digest in credential_digests_to_revoke or ()
    )
    # The v1/v2 cache keys were derived with a fast SHA-256 digest.  They are
    # no longer accepted by the middleware, but remove them during startup so
    # stale positive authentication entries cannot survive an upgrade.
    keys.update(cache_service.scan_keys(_LEGACY_VERIFIED_CACHE_PATTERN))
    keys.update(cache_service.scan_keys(_PREVIOUS_VERIFIED_CACHE_PATTERN))
    keys.update(cache_service.scan_keys(_LEGACY_FLOW_CACHE_PATTERN))
    for key in keys:
        cache_service.delete(key)


def synchronize_deployment_bootstrap_app(
    credentials: TenantBootstrapCredentials | None = None,
) -> None:
    credentials = credentials or load_tenant_bootstrap_credentials()
    credential_digests_to_revoke: set[str] = set()
    with session_getter() as session:
        synchronize_bootstrap_app(
            session,
            credentials,
            credential_digests_to_revoke=credential_digests_to_revoke,
        )
    # session_getter commits before returning. Cache revocation is deliberately
    # fail-closed: a replica must not become ready while disclosed positive auth
    # entries or old secret-bearing flow objects remain usable.
    invalidate_legacy_bootstrap_caches(credentials, credential_digests_to_revoke)
    logger.info("Workflow tenant bootstrap credentials synchronized")
