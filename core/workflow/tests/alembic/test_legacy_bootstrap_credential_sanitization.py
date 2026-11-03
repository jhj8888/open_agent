"""Migration tests for removal of the disclosed tenant credential pair."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "2026_08_27_1000-a91e02d64b77_disable_legacy_bootstrap_credentials.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "disable_legacy_bootstrap_credentials", MIGRATION_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load migration: {MIGRATION_PATH}")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_chain_and_downgrade_never_restores_disclosed_values() -> None:
    migration = _load_migration()

    assert migration.revision == "a91e02d64b77"
    assert migration.down_revision == "c7e8a4f19d32"
    assert migration.downgrade() is None


def test_upgrade_removes_only_exact_disclosed_values_from_both_protocols(
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

    disclosed = (
        f'{{"apiKey":"{migration._LEGACY_KEY}",'
        f'"apiSecret":"{migration._LEGACY_SECRET}"}}'
    )
    custom = '{"apiKey":"customer-key","apiSecret":"customer-secret"}'
    with engine.begin() as connection:
        connection.execute(
            flow.insert(),
            [
                {"id": 1, "data": disclosed, "release_data": disclosed},
                {"id": 2, "data": custom, "release_data": custom},
            ],
        )
        monkeypatch.setattr(migration.op, "execute", connection.execute)

        migration.upgrade()

        rows = {
            row.id: row
            for row in connection.execute(sa.select(flow).order_by(flow.c.id))
        }

    assert migration._LEGACY_KEY not in rows[1].data
    assert migration._LEGACY_SECRET not in rows[1].data
    assert migration._LEGACY_KEY not in rows[1].release_data
    assert migration._LEGACY_SECRET not in rows[1].release_data
    assert rows[2].data == custom
    assert rows[2].release_data == custom
