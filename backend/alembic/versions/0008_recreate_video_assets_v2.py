"""Recreate video_assets + transcript_segments (v2)

Revision ID: 0008_recreate_video_assets_v2
Revises: 0007_video_assets_segments
Create Date: 2026-01-05

This project previously used Bunny-backed fields on `video_assets`. We have hard-deleted
that ingestion path. This migration resets the schema to the new local-upload + transcription
pipeline contract (S3/MinIO + ffmpeg + Runpod + whisper-timestamped).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0008_recreate_video_assets_v2"
down_revision = "0007_video_assets_segments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop in dependency order.
    op.drop_table("transcript_segments")
    op.drop_table("video_assets")

    op.create_table(
        "video_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default=sa.text("'local'")),
        # Upload metadata (source file is REQUIRED).
        sa.Column("source_file_key", sa.String(length=1024), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        # Transcription state (PR3 will populate these).
        sa.Column("status", sa.String(length=64), nullable=False, server_default=sa.text("'uploaded'")),
        sa.Column("transcription_job_id", sa.String(length=255), nullable=True),
        sa.Column("transcription_error", sa.Text(), nullable=True),
        sa.Column("transcription_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transcription_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transcript_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_id"], ["course_contents.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_video_assets_course_id", "video_assets", ["course_id"], unique=False)
    op.create_index("ix_video_assets_content_id", "video_assets", ["content_id"], unique=False)
    op.create_index("ix_video_assets_provider", "video_assets", ["provider"], unique=False)
    op.create_index("ix_video_assets_status", "video_assets", ["status"], unique=False)
    op.create_index("ix_video_assets_source_file_key", "video_assets", ["source_file_key"], unique=False)
    # Prevent duplicates within a course for the same uploaded object.
    op.create_index(
        "uq_video_assets_course_source_key",
        "video_assets",
        ["course_id", "source_file_key"],
        unique=True,
    )

    op.create_table(
        "transcript_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_sec", sa.Float(), nullable=False),
        sa.Column("end_sec", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language_code", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_asset_id"], ["video_assets.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_transcript_segments_course_id", "transcript_segments", ["course_id"], unique=False)
    op.create_index(
        "ix_transcript_segments_video_asset_id",
        "transcript_segments",
        ["video_asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_transcript_segments_video_asset_id_start_sec",
        "transcript_segments",
        ["video_asset_id", "start_sec"],
        unique=False,
    )
    op.create_index(
        "ix_transcript_segments_language_code",
        "transcript_segments",
        ["language_code"],
        unique=False,
    )


def downgrade() -> None:
    # Revert to the prior Bunny-era schema by re-running the prior revision logic is not supported.
    # This project uses hard-deletes for the Bunny path, so downgrade is intentionally destructive.
    op.drop_index("ix_transcript_segments_language_code", table_name="transcript_segments")
    op.drop_index("ix_transcript_segments_video_asset_id_start_sec", table_name="transcript_segments")
    op.drop_index("ix_transcript_segments_video_asset_id", table_name="transcript_segments")
    op.drop_index("ix_transcript_segments_course_id", table_name="transcript_segments")
    op.drop_table("transcript_segments")

    op.drop_index("uq_video_assets_course_source_key", table_name="video_assets")
    op.drop_index("ix_video_assets_source_file_key", table_name="video_assets")
    op.drop_index("ix_video_assets_status", table_name="video_assets")
    op.drop_index("ix_video_assets_provider", table_name="video_assets")
    op.drop_index("ix_video_assets_content_id", table_name="video_assets")
    op.drop_index("ix_video_assets_course_id", table_name="video_assets")
    op.drop_table("video_assets")


