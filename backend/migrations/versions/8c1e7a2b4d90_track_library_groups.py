"""add music libraries and track membership

Revision ID: 8c1e7a2b4d90
Revises: 4f2a9d71c6e0
Create Date: 2026-07-26 00:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "8c1e7a2b4d90"
down_revision: str | Sequence[str] | None = "4f2a9d71c6e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "music_libraries",
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        sa.table(
            "music_libraries",
            sa.column("name", sa.String(length=80)),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
        [{"name": "default", "created_at": now, "updated_at": now}],
    )

    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "library_group",
                sa.String(length=80),
                server_default="default",
                nullable=False,
            )
        )
        batch_op.create_index(
            batch_op.f("ix_tracks_library_group"),
            ["library_group"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_tracks_library_group_music_libraries",
            "music_libraries",
            ["library_group"],
            ["name"],
            ondelete="RESTRICT",
            onupdate="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_tracks_library_group_music_libraries",
            type_="foreignkey",
        )
        batch_op.drop_index(batch_op.f("ix_tracks_library_group"))
        batch_op.drop_column("library_group")
    op.drop_table("music_libraries")
