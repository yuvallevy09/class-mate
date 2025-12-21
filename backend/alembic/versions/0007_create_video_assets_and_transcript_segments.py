"""Create video_assets + transcript_segments tables

Revision ID: 0007_video_assets_segments
Revises: 0006_create_chat_tables
Create Date: 2025-12-20

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0007_video_assets_segments"
down_revision = "0006_create_chat_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("video_library_id", sa.Integer(), nullable=True),
        sa.Column("video_guid", sa.String(length=128), nullable=False),
        sa.Column("pull_zone_url", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("last_webhook_status", sa.Integer(), nullable=True),
        sa.Column("last_webhook_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captions_language_code", sa.String(length=16), nullable=True),
        sa.Column("captions_vtt_url", sa.Text(), nullable=True),
        sa.Column("captions_ready_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_video_assets_video_guid", "video_assets", ["video_guid"], unique=False)
    op.create_index("ix_video_assets_status", "video_assets", ["status"], unique=False)

    # Prevent duplicate provider+guid within the same course.
    op.create_index(
        "uq_video_assets_course_provider_guid",
        "video_assets",
        ["course_id", "provider", "video_guid"],
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
    op.create_index("ix_transcript_segments_video_asset_id", "transcript_segments", ["video_asset_id"], unique=False)
    op.create_index("ix_transcript_segments_language_code", "transcript_segments", ["language_code"], unique=False)
    op.create_index(
        "ix_transcript_segments_video_asset_id_start_sec",
        "transcript_segments",
        ["video_asset_id", "start_sec"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_transcript_segments_video_asset_id_start_sec", table_name="transcript_segments")
    op.drop_index("ix_transcript_segments_language_code", table_name="transcript_segments")
    op.drop_index("ix_transcript_segments_video_asset_id", table_name="transcript_segments")
    op.drop_index("ix_transcript_segments_course_id", table_name="transcript_segments")
    op.drop_table("transcript_segments")

    op.drop_index("uq_video_assets_course_provider_guid", table_name="video_assets")
    op.drop_index("ix_video_assets_status", table_name="video_assets")
    op.drop_index("ix_video_assets_video_guid", table_name="video_assets")
    op.drop_index("ix_video_assets_provider", table_name="video_assets")
    op.drop_index("ix_video_assets_content_id", table_name="video_assets")
    op.drop_index("ix_video_assets_course_id", table_name="video_assets")
    op.drop_table("video_assets")


