"""persist upload target library

Revision ID: d3f6a8219b47
Revises: 8c1e7a2b4d90
Create Date: 2026-07-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3f6a8219b47"
down_revision: str | Sequence[str] | None = "8c1e7a2b4d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("upload_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "target_library",
                sa.String(length=80),
                server_default="default",
                nullable=True,
            )
        )
        batch_op.create_index(
            batch_op.f("ix_upload_jobs_target_library"),
            ["target_library"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_upload_jobs_target_library_music_libraries",
            "music_libraries",
            ["target_library"],
            ["name"],
            ondelete="SET NULL",
            onupdate="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("upload_jobs", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_upload_jobs_target_library_music_libraries",
            type_="foreignkey",
        )
        batch_op.drop_index(batch_op.f("ix_upload_jobs_target_library"))
        batch_op.drop_column("target_library")
