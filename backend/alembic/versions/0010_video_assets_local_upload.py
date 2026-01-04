"""Support local uploaded video assets

Revision ID: 0010_video_assets_local
Revises: 0009_video_chapters_source
Create Date: 2026-01-04

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0010_video_assets_local"
down_revision = "0009_video_chapters_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Local uploads don't have a Bunny GUID; make it optional.
    op.alter_column("video_assets", "video_guid", existing_type=sa.String(length=128), nullable=True)

    # Add local-upload fields + transcription bookkeeping.
    op.add_column("video_assets", sa.Column("source_file_key", sa.String(length=1024), nullable=True))
    op.add_column("video_assets", sa.Column("original_filename", sa.String(length=255), nullable=True))
    op.add_column("video_assets", sa.Column("mime_type", sa.String(length=255), nullable=True))
    op.add_column("video_assets", sa.Column("size_bytes", sa.BigInteger(), nullable=True))

    # Optional: store extracted audio for retries/debugging.
    op.add_column("video_assets", sa.Column("audio_file_key", sa.String(length=1024), nullable=True))

    # Runpod job tracking / error reporting.
    op.add_column("video_assets", sa.Column("transcription_job_id", sa.String(length=255), nullable=True))
    op.add_column("video_assets", sa.Column("transcription_error", sa.Text(), nullable=True))
    op.add_column("video_assets", sa.Column("transcription_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("video_assets", sa.Column("transcription_completed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_index("ix_video_assets_source_file_key", "video_assets", ["source_file_key"], unique=False)

    # Enforce per-course uniqueness for local uploaded files.
    # (Use a partial unique index so legacy rows with NULL source_file_key don't conflict.)
    op.create_index(
        "uq_video_assets_course_provider_source_key",
        "video_assets",
        ["course_id", "provider", "source_file_key"],
        unique=True,
        postgresql_where=sa.text("source_file_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_video_assets_course_provider_source_key", table_name="video_assets")
    op.drop_index("ix_video_assets_source_file_key", table_name="video_assets")

    op.drop_column("video_assets", "transcription_completed_at")
    op.drop_column("video_assets", "transcription_started_at")
    op.drop_column("video_assets", "transcription_error")
    op.drop_column("video_assets", "transcription_job_id")
    op.drop_column("video_assets", "audio_file_key")
    op.drop_column("video_assets", "size_bytes")
    op.drop_column("video_assets", "mime_type")
    op.drop_column("video_assets", "original_filename")
    op.drop_column("video_assets", "source_file_key")

    op.alter_column("video_assets", "video_guid", existing_type=sa.String(length=128), nullable=False)


