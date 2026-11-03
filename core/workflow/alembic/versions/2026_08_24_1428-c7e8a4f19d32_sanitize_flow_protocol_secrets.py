"""sanitize legacy deployment secrets from flow protocols

Revision ID: c7e8a4f19d32
Revises: fdacc27881b5
Create Date: 2026-08-24 14:28:00.000000

"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

# revision identifiers, used by Alembic.
revision: str = "c7e8a4f19d32"
down_revision: Union[str, None] = "fdacc27881b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BATCH_SIZE = 100
_MAX_ROW_UPDATE_ATTEMPTS = 4

_SANDBOX_ALLOWED_KEYS = {
    "enabled",
    "uid",
    "spaceid",
    "workflowid",
    "runid",
    "nodeid",
}
_LEGACY_DEPLOYMENT_FIELDS = {
    "artifactuploadtoken",
    "artifactuploadurl",
    "runtimecredentialtoken",
    "runtimecredentialurl",
    "runtimeconfigtoken",
    "runtimeconfigurl",
    "runtimeconfigurationtoken",
    "runtimeconfigurationurl",
    "sandboxapikey",
    "sandboxartifactuploadtoken",
    "sandboxartifactuploadurl",
    "sandboxruntimecredentialtoken",
    "sandboxruntimecredentialurl",
    "sandboxruntimeconfigtoken",
    "sandboxruntimeconfigurl",
    "skillsandboxartifactuploadtoken",
    "skillsandboxartifactuploadurl",
    "skillsandboxruntimecredentialtoken",
    "skillsandboxruntimecredentialurl",
    "skillsandboxruntimeconfigtoken",
    "skillsandboxruntimeconfigurl",
}


def _normalized_key(key: Any) -> str:
    return "".join(character for character in str(key).lower() if character.isalnum())


def _sanitize_sandbox(value: Any) -> dict[Any, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: _sanitize_value(item)
        for key, item in value.items()
        if _normalized_key(key) in _SANDBOX_ALLOWED_KEYS
    }


def _sanitize_serialized_value(value: str) -> str:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        return value
    if not isinstance(decoded, (dict, list)):
        return value
    try:
        sanitized = _sanitize_value(decoded)
    except RecursionError:
        return value
    if sanitized == decoded:
        return value
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = _normalized_key(key)
            if normalized in _LEGACY_DEPLOYMENT_FIELDS:
                continue
            if normalized == "sandbox":
                sanitized[key] = _sanitize_sandbox(item)
            else:
                sanitized[key] = _sanitize_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_serialized_value(value)
    return value


def _sanitize_protocol_text(value: Any) -> str | None:
    """Return updated JSON text, or None for invalid/unchanged database values."""
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        return None
    if not isinstance(decoded, (dict, list)):
        return None
    try:
        sanitized = _sanitize_value(decoded)
    except RecursionError:
        return None
    if sanitized == decoded:
        return None
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))


def upgrade() -> None:
    bind = op.get_bind()
    flow = sa.table(
        "flow",
        sa.column("id", sa.BigInteger()),
        sa.column("data", sa.Text()),
        sa.column("release_data", sa.Text()),
    )

    last_id: int | None = None
    while True:
        query = (
            sa.select(flow.c.id, flow.c.data, flow.c.release_data)
            .order_by(flow.c.id.asc())
            .limit(_BATCH_SIZE)
        )
        if last_id is not None:
            query = query.where(flow.c.id > last_id)
        rows = list(bind.execute(query).mappings())
        if not rows:
            break
        for row in rows:
            _sanitize_row_with_retry(bind, flow, dict(row))
        last_id = int(rows[-1]["id"])


def _sanitize_row_with_retry(bind: Any, flow: Any, row: dict[str, Any]) -> None:
    row_id = row["id"]
    current = row
    for attempt in range(_MAX_ROW_UPDATE_ATTEMPTS):
        changes: dict[str, str] = {}
        for column_name in ("data", "release_data"):
            sanitized = _sanitize_protocol_text(current[column_name])
            if sanitized is not None:
                changes[column_name] = sanitized
        if not changes:
            return

        predicates = [flow.c.id == row_id]
        # Compare both protocol columns, including a currently clean sibling column.
        # Otherwise a concurrent write could add a secret to that sibling between the
        # SELECT and UPDATE while the CAS still succeeds for the changed column.
        for column_name in ("data", "release_data"):
            old_value = current[column_name]
            column = getattr(flow.c, column_name)
            predicates.append(
                column.is_(None) if old_value is None else column == old_value
            )
        result = bind.execute(sa.update(flow).where(*predicates).values(**changes))
        if result.rowcount == 1:
            return
        if result.rowcount not in (0, None):
            raise RuntimeError(
                "Flow protocol sanitization affected an unexpected number of rows"
            )
        if attempt + 1 >= _MAX_ROW_UPDATE_ATTEMPTS:
            break
        refreshed = (
            bind.execute(
                sa.select(flow.c.id, flow.c.data, flow.c.release_data).where(
                    flow.c.id == row_id
                )
            )
            .mappings()
            .first()
        )
        if refreshed is None:
            raise RuntimeError("Flow disappeared during protocol sanitization")
        current = dict(refreshed)
    raise RuntimeError("Flow protocol changed repeatedly during sanitization")


def downgrade() -> None:
    raise RuntimeError(
        "This migration is irreversible: removed workflow sandbox deployment "
        "credentials cannot be reconstructed safely."
    )
