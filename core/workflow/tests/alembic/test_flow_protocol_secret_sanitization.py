"""Data migration tests for removal of persisted sandbox deployment secrets."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import sqlalchemy as sa

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "2026_08_24_1428-c7e8a4f19d32_sanitize_flow_protocol_secrets.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "sanitize_flow_protocols", MIGRATION_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load migration: {MIGRATION_PATH}")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_chain_and_irreversible_downgrade() -> None:
    migration = _load_migration()
    assert migration.revision == "c7e8a4f19d32"
    assert migration.down_revision == "fdacc27881b5"
    with pytest.raises(RuntimeError, match="irreversible"):
        migration.downgrade()


def test_upgrade_updates_only_changed_valid_json_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    flow = sa.Table(
        "flow",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("data", sa.Text),
        sa.Column("release_data", sa.Text),
    )
    metadata.create_all(engine)

    changed = json.dumps(
        {
            "api_key": "valid-model-key",
            "sandbox": {
                "enabled": True,
                "workflowId": "flow-1",
                "apiKey": "sandbox-secret",
                "artifactUploadToken": "artifact-secret",
                "runtimeConfigUrl": "http://hub/runtime-config",
            },
            "nested": {"runtime_credential_token": "runtime-secret"},
        },
        indent=2,
    )
    unchanged = '{ "apiKey": "valid-model-key", "data": {"nodes": []} }'
    invalid = '{"sandbox":'
    update_statements: list[tuple[str, tuple[object, ...]]] = []

    @sa.event.listens_for(engine, "before_cursor_execute")
    def capture_updates(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("UPDATE"):
            assert isinstance(parameters, tuple)
            update_statements.append((statement, parameters))

    with engine.begin() as connection:
        connection.execute(
            flow.insert(),
            [
                {"id": 1, "data": changed, "release_data": changed},
                {"id": 2, "data": unchanged, "release_data": unchanged},
                {"id": 3, "data": invalid, "release_data": invalid},
            ],
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()
        rows = {
            row.id: row
            for row in connection.execute(sa.select(flow).order_by(flow.c.id))
        }

    migrated = json.loads(rows[1].data)
    assert migrated == {
        "api_key": "valid-model-key",
        "sandbox": {"enabled": True, "workflowId": "flow-1"},
        "nested": {},
    }
    assert json.loads(rows[1].release_data) == migrated
    assert rows[2].data == unchanged
    assert rows[2].release_data == unchanged
    assert rows[3].data == invalid
    assert rows[3].release_data == invalid
    assert len(update_statements) == 1
    assert "sandbox-secret" not in update_statements[0][0]
    assert "artifact-secret" not in update_statements[0][0]
    # Old values remain bound (never interpolated) solely as optimistic CAS predicates;
    # replacement values are the first two parameters and are fully sanitized.
    replacement_parameters = update_statements[0][1][:2]
    assert "sandbox-secret" not in repr(replacement_parameters)
    assert "artifact-secret" not in repr(replacement_parameters)
    assert "runtime-secret" not in repr(replacement_parameters)


def test_upgrade_uses_keyset_batches_and_sanitizes_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    monkeypatch.setattr(migration, "_BATCH_SIZE", 2)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    flow = sa.Table(
        "flow",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("data", sa.Text),
        sa.Column("release_data", sa.Text),
    )
    metadata.create_all(engine)
    selects: list[str] = []

    @sa.event.listens_for(engine, "before_cursor_execute")
    def capture_selects(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    secret = json.dumps({"sandbox": {"artifactUploadToken": "secret"}})
    with engine.begin() as connection:
        connection.execute(
            flow.insert(),
            [
                {"id": row_id, "data": secret, "release_data": secret}
                for row_id in range(1, 6)
            ],
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()
        migration_selects = list(selects)
        rows = list(connection.execute(sa.select(flow).order_by(flow.c.id)))

    assert len(migration_selects) == 4
    assert all("LIMIT" in statement.upper() for statement in migration_selects)
    assert all(json.loads(row.data) == {"sandbox": {}} for row in rows)


def test_upgrade_retries_cas_conflict_and_preserves_concurrent_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    flow = sa.Table(
        "flow",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("data", sa.Text),
        sa.Column("release_data", sa.Text),
    )
    metadata.create_all(engine)
    initial = json.dumps({"sandbox": {"artifactUploadToken": "old-secret"}})
    concurrent = json.dumps(
        {
            "concurrent": "preserve-me",
            "sandbox": {"artifactUploadToken": "new-secret", "enabled": True},
        }
    )

    with engine.begin() as connection:
        connection.execute(
            flow.insert().values(id=1, data=initial, release_data=initial)
        )

        class ConflictOnce:
            conflicted = False

            def execute(
                self, statement: object, *args: object, **kwargs: object
            ) -> object:
                if isinstance(statement, sa.sql.dml.Update) and not self.conflicted:
                    self.conflicted = True
                    connection.execute(
                        flow.update().where(flow.c.id == 1).values(data=concurrent)
                    )
                return connection.execute(statement, *args, **kwargs)

        monkeypatch.setattr(migration.op, "get_bind", lambda: ConflictOnce())
        migration.upgrade()
        row = connection.execute(sa.select(flow).where(flow.c.id == 1)).one()

    assert json.loads(row.data) == {
        "concurrent": "preserve-me",
        "sandbox": {"enabled": True},
    }
    assert json.loads(row.release_data) == {"sandbox": {}}


def test_upgrade_cas_detects_concurrent_write_to_initially_clean_sibling_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    flow = sa.Table(
        "flow",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("data", sa.Text),
        sa.Column("release_data", sa.Text),
    )
    metadata.create_all(engine)
    secret = json.dumps({"sandbox": {"artifactUploadToken": "secret"}})
    clean = json.dumps({"nodes": []})
    concurrent = json.dumps(
        {"concurrent": True, "sandbox": {"runtimeCredentialToken": "new-secret"}}
    )

    with engine.begin() as connection:
        connection.execute(flow.insert().values(id=1, data=secret, release_data=clean))

        class ConflictOnce:
            conflicted = False

            def execute(
                self, statement: object, *args: object, **kwargs: object
            ) -> object:
                if isinstance(statement, sa.sql.dml.Update) and not self.conflicted:
                    self.conflicted = True
                    connection.execute(
                        flow.update()
                        .where(flow.c.id == 1)
                        .values(release_data=concurrent)
                    )
                return connection.execute(statement, *args, **kwargs)

        monkeypatch.setattr(migration.op, "get_bind", lambda: ConflictOnce())
        migration.upgrade()
        row = connection.execute(sa.select(flow).where(flow.c.id == 1)).one()

    assert json.loads(row.data) == {"sandbox": {}}
    assert json.loads(row.release_data) == {"concurrent": True, "sandbox": {}}


def test_upgrade_aborts_after_bounded_repeated_cas_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    flow = sa.Table(
        "flow",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("data", sa.Text),
        sa.Column("release_data", sa.Text),
    )
    metadata.create_all(engine)
    secret = json.dumps({"sandbox": {"artifactUploadToken": "must-not-leak"}})

    with engine.begin() as connection:
        connection.execute(flow.insert().values(id=1, data=secret, release_data=None))

        class AlwaysConflicts:
            update_attempts = 0

            def execute(
                self, statement: object, *args: object, **kwargs: object
            ) -> object:
                if isinstance(statement, sa.sql.dml.Update):
                    self.update_attempts += 1
                    return SimpleNamespace(rowcount=0)
                return connection.execute(statement, *args, **kwargs)

        wrapper = AlwaysConflicts()
        monkeypatch.setattr(migration.op, "get_bind", lambda: wrapper)
        with pytest.raises(RuntimeError, match="changed repeatedly") as exc_info:
            migration.upgrade()

    assert wrapper.update_attempts == migration._MAX_ROW_UPDATE_ATTEMPTS
    assert "must-not-leak" not in str(exc_info.value)
