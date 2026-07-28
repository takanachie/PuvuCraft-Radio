"""add one opaque player credential generation per user

Revision ID: a91c0f4e2d73
Revises: d3f6a8219b47
Create Date: 2026-07-29 01:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91c0f4e2d73"
down_revision: str | Sequence[str] | None = "d3f6a8219b47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing users intentionally remain NULL and opt in by regenerating once.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("player_key_created_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("player_key_generation", sa.LargeBinary(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("player_key_generation")
        batch_op.drop_column("player_key_created_at")
