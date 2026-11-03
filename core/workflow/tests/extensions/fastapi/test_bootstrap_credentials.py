from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine  # type: ignore

from workflow.domain.models.ai_app import App
from workflow.domain.models.app_source import AppSource
from workflow.extensions.fastapi.lifespan import (
    bootstrap_credentials as bootstrap_module,
)
from workflow.extensions.fastapi.lifespan.bootstrap_credentials import (
    BOOTSTRAP_TENANT_ID,
    LEGACY_TENANT_KEY,
    LEGACY_TENANT_SECRET,
    TenantBootstrapCredentials,
    invalidate_legacy_bootstrap_caches,
    load_tenant_bootstrap_credentials,
    synchronize_bootstrap_app,
    synchronize_deployment_bootstrap_app,
)
from workflow.utils.credentials import credential_cache_key


@pytest.fixture
def engine() -> Engine:
    database_engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        database_engine, tables=[AppSource.__table__, App.__table__]
    )
    with Session(database_engine) as session:
        session.add(
            AppSource(
                id=1,
                source=1,
                source_id="admin",
                description="星辰",
                create_at=datetime.now(),
                update_at=datetime.now(),
            )
        )
        session.commit()
    return database_engine


def credentials() -> TenantBootstrapCredentials:
    return TenantBootstrapCredentials(BOOTSTRAP_TENANT_ID, "k" * 48, "s" * 48)


def deployment_owned_app(**overrides: Any) -> App:
    values = {
        "id": 1,
        "name": "星辰",
        "alias_id": BOOTSTRAP_TENANT_ID,
        "is_tenant": 1,
        "source": 1,
        "actual_source": 1,
        "plat_release_auth": 1,
        "status": 1,
        "create_by": 1,
        "update_by": 1,
    }
    values.update(overrides)
    return App(**values)


def test_synchronize_bootstrap_app_creates_reserved_row(engine: Engine) -> None:
    with Session(engine) as session:
        synchronize_bootstrap_app(session, credentials())
        session.commit()

        app = session.get(App, 1)
        assert app is not None
        assert app.alias_id == BOOTSTRAP_TENANT_ID
        assert app.api_key == "k" * 48
        assert app.api_secret == "s" * 48


def test_synchronize_bootstrap_app_rotates_only_reserved_row(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            deployment_owned_app(
                api_key=LEGACY_TENANT_KEY,
                api_secret=LEGACY_TENANT_SECRET,
                create_at=datetime.now(),
                update_at=datetime.now(),
            )
        )
        session.add(
            App(
                id=2,
                name="user-app",
                alias_id="user-app",
                api_key="user-key",
                create_by=2,
                update_by=2,
            )
        )
        session.commit()

        synchronize_bootstrap_app(session, credentials())
        session.commit()

        bootstrap = session.get(App, 1)
        user_app = session.get(App, 2)
        assert bootstrap is not None and bootstrap.api_key == "k" * 48
        assert user_app is not None and user_app.api_key == "user-key"


def test_synchronize_bootstrap_app_rejects_alias_collision(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            App(
                id=2,
                name="user-app",
                alias_id=BOOTSTRAP_TENANT_ID,
                create_by=2,
                update_by=2,
            )
        )
        session.commit()

        with pytest.raises(RuntimeError, match="alias is already in use"):
            synchronize_bootstrap_app(session, credentials())


def test_synchronize_bootstrap_app_rotates_strong_custom_pair(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            deployment_owned_app(
                api_key="x" * 48,
                api_secret="y" * 48,
            )
        )
        session.commit()

        synchronize_bootstrap_app(session, credentials())
        session.commit()

        app = session.get(App, 1)
        assert app is not None
        assert app.api_key == "k" * 48
        assert app.api_secret == "s" * 48


def test_second_rotation_revokes_old_digest_only_after_commit(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_key = "x" * 48
    old_secret = "y" * 48
    with Session(engine) as session:
        session.add(deployment_owned_app(api_key=old_key, api_secret=old_secret))
        session.commit()

    commit_state = {"committed": False}

    @contextmanager
    def committed_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session
            session.commit()
            commit_state["committed"] = True

    class _CommitAwareCache(_RecordingCache):
        def delete(self, key: str) -> None:
            assert commit_state["committed"]
            super().delete(key)

    cache = _CommitAwareCache()
    monkeypatch.setattr(bootstrap_module, "session_getter", committed_session)
    monkeypatch.setattr(bootstrap_module, "get_cache_service", lambda: cache)

    synchronize_deployment_bootstrap_app(credentials())

    old_digest = credential_cache_key(f"{old_key}:{old_secret}")
    assert f"workflow:app:verified_credential:v3:{old_digest}" in cache.deleted
    assert "workflow:app:verified_credential:v2:old-entry" in cache.deleted
    with Session(engine) as session:
        app = session.get(App, 1)
        assert app is not None
        assert (app.api_key, app.api_secret) == ("k" * 48, "s" * 48)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("is_tenant", 0),
        ("source", 0),
        ("actual_source", 0),
        ("plat_release_auth", 0),
        ("status", 0),
    ],
)
def test_synchronize_bootstrap_app_rejects_tampered_reserved_identity(
    engine: Engine, field: str, value: int
) -> None:
    with Session(engine) as session:
        session.add(deployment_owned_app(**{field: value}))
        session.commit()

        with pytest.raises(RuntimeError, match="identity is invalid"):
            synchronize_bootstrap_app(session, credentials())


def test_synchronize_bootstrap_app_accepts_matching_custom_pair(
    engine: Engine,
) -> None:
    configured = credentials()
    with Session(engine) as session:
        session.add(
            deployment_owned_app(
                api_key=configured.api_key,
                api_secret=configured.api_secret,
            )
        )
        session.commit()

        synchronize_bootstrap_app(session, configured)
        session.commit()

        app = session.get(App, 1)
        assert app is not None
        assert (app.api_key, app.api_secret) == (
            configured.api_key,
            configured.api_secret,
        )


def test_load_tenant_bootstrap_credentials_from_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key_file = tmp_path / "tenant-key"
    secret_file = tmp_path / "tenant-secret"
    key_file.write_text("k" * 48 + "\n", encoding="utf-8")
    secret_file.write_text("s" * 48 + "\n", encoding="utf-8")
    monkeypatch.setenv("TENANT_ID", BOOTSTRAP_TENANT_ID)
    monkeypatch.delenv("TENANT_KEY", raising=False)
    monkeypatch.delenv("TENANT_SECRET", raising=False)
    monkeypatch.setenv("TENANT_KEY_FILE", str(key_file))
    monkeypatch.setenv("TENANT_SECRET_FILE", str(secret_file))

    loaded = load_tenant_bootstrap_credentials()

    assert loaded == credentials()


def test_load_tenant_bootstrap_credentials_rejects_legacy_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TENANT_ID", BOOTSTRAP_TENANT_ID)
    monkeypatch.setenv("TENANT_KEY", LEGACY_TENANT_KEY)
    monkeypatch.setenv("TENANT_SECRET", LEGACY_TENANT_SECRET)

    with pytest.raises(RuntimeError, match="legacy"):
        load_tenant_bootstrap_credentials()


def test_load_tenant_bootstrap_credentials_rejects_header_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TENANT_ID", BOOTSTRAP_TENANT_ID)
    monkeypatch.setenv("TENANT_KEY", "k" * 47 + ":")
    monkeypatch.setenv("TENANT_SECRET", "s" * 48)

    with pytest.raises(RuntimeError, match="unsafe"):
        load_tenant_bootstrap_credentials()


class _RecordingCache:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def scan_keys(self, pattern: str) -> list[str]:
        if pattern == bootstrap_module._LEGACY_VERIFIED_CACHE_PATTERN:
            return [
                "workflow:app:verified_credential:8f36ac6b8a917de90c78ca6828908790c0f0df33a319e3b793a35e2dc988f18f"
            ]
        if pattern == bootstrap_module._PREVIOUS_VERIFIED_CACHE_PATTERN:
            return ["workflow:app:verified_credential:v2:old-entry"]
        if pattern == "workflow:flow_info:v2:*":
            return [
                "workflow:flow_info:v2:1",
                "workflow:flow_info:v2:1:latest",
            ]
        raise AssertionError(f"unexpected cache scan pattern: {pattern}")

    def delete(self, key: str) -> None:
        self.deleted.append(key)


def test_invalidate_legacy_bootstrap_caches_revokes_all_old_namespaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _RecordingCache()
    monkeypatch.setattr(bootstrap_module, "get_cache_service", lambda: cache)

    invalidate_legacy_bootstrap_caches(credentials())

    assert "workflow:app_info:680ab54f" in cache.deleted
    assert "workflow:app_info:v2:680ab54f" in cache.deleted
    assert "workflow:app:api_key:7b709739e8da44536127a333c7603a83" in cache.deleted
    legacy_digest = credential_cache_key(f"{LEGACY_TENANT_KEY}:{LEGACY_TENANT_SECRET}")
    assert f"workflow:app:verified_credential:v3:{legacy_digest}" in cache.deleted
    assert "workflow:app:verified_credential:v2:old-entry" in cache.deleted
    assert "workflow:flow_info:v2:1" in cache.deleted
    assert "workflow:flow_info:v2:1:latest" in cache.deleted
    assert any(
        key.startswith("workflow:app:verified_credential:")
        and key.endswith(
            "8f36ac6b8a917de90c78ca6828908790c0f0df33a319e3b793a35e2dc988f18f"
        )
        for key in cache.deleted
    )
