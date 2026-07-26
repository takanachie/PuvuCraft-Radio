"""add persistent upload queue and storage locations

Revision ID: 4f2a9d71c6e0
Revises: c8147810aadc
Create Date: 2026-07-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4f2a9d71c6e0"
down_revision: str | Sequence[str] | None = "c8147810aadc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("storage_id", sa.String(length=80), server_default="primary", nullable=False)
        )
        batch_op.add_column(
            sa.Column("sample_rate", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("channels", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(
            sa.Column("bits_per_sample", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("normalized", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.create_index(batch_op.f("ix_tracks_storage_id"), ["storage_id"], unique=False)

    op.create_table(
        "upload_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("declared_size_bytes", sa.Integer(), nullable=False),
        sa.Column("bytes_received", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("temp_name", sa.String(length=255), nullable=True),
        sa.Column("storage_id", sa.String(length=80), nullable=True),
        sa.Column("storage_name", sa.String(length=255), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("duplicate", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("client_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("upload_jobs", schema=None) as batch_op:
        batch_op.create_index(
            "ix_upload_jobs_status_created", ["status", "created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_upload_jobs_client_id"), ["client_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_upload_jobs_lease_expires_at"),
            ["lease_expires_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_upload_jobs_owner_user_id"),
            ["owner_user_id"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_upload_jobs_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_upload_jobs_track_id"), ["track_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("upload_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_upload_jobs_track_id"))
        batch_op.drop_index(batch_op.f("ix_upload_jobs_status"))
        batch_op.drop_index(batch_op.f("ix_upload_jobs_owner_user_id"))
        batch_op.drop_index(batch_op.f("ix_upload_jobs_lease_expires_at"))
        batch_op.drop_index(batch_op.f("ix_upload_jobs_client_id"))
        batch_op.drop_index("ix_upload_jobs_status_created")
    op.drop_table("upload_jobs")

    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tracks_storage_id"))
        batch_op.drop_column("normalized")
        batch_op.drop_column("bits_per_sample")
        batch_op.drop_column("channels")
        batch_op.drop_column("sample_rate")
        batch_op.drop_column("storage_id")
