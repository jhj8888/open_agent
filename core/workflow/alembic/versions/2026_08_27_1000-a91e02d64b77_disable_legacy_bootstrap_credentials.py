"""sanitize published legacy bootstrap credentials from workflow protocols

Revision ID: a91e02d64b77
Revises: c7e8a4f19d32
Create Date: 2026-08-27 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "a91e02d64b77"
down_revision: Union[str, None] = "c7e8a4f19d32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_KEY = "7b709739e8da44536127a333c7603a83"
_LEGACY_SECRET = "NjhmY2NmM2NkZDE4MDFlNmM5ZjcyZjMy"


def upgrade() -> None:
    flow = sa.table(
        "flow",
        sa.column("data", sa.Text),
        sa.column("release_data", sa.Text),
    )
    for column_name in ("data", "release_data"):
        column = getattr(flow.c, column_name)
        op.execute(
            flow.update()
            .where(
                sa.or_(
                    column.like(f"%{_LEGACY_KEY}%"),
                    column.like(f"%{_LEGACY_SECRET}%"),
                )
            )
            .values(
                **{
                    column_name: sa.func.replace(
                        sa.func.replace(column, _LEGACY_KEY, ""),
                        _LEGACY_SECRET,
                        "",
                    )
                }
            )
        )


def downgrade() -> None:
    # Never restore a credential that has been publicly disclosed.
    pass
