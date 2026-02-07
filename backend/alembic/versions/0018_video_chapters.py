"""Create video_chapters table

Revision ID: 0018_video_chapters
Revises: 0017_chat_msg_citations
Create Date: 2026-02-07

Adds a first-class chapters artifact for video transcripts. Chapters are derived from
transcript segments and enable structured navigation and citation UX.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0018_video_chapters"
down_revision = "0017_chat_msg_citations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_chapters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("video_asset_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("language_code", sa.String(length=16), nullable=False, index=True),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("start_sec", sa.Float(), nullable=False),
        sa.Column("end_sec", sa.Float(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("artifact_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_hash", sa.String(length=128), nullable=True),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
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
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["video_asset_id"], ["video_assets.id"], ondelete="CASCADE"),
    )

    op.create_index(
        "ix_video_chapters_video_asset_id_language_index",
        "video_chapters",
        ["video_asset_id", "language_code", "chapter_index"],
        unique=True,
    )
    op.create_index(
        "ix_video_chapters_video_asset_id_language_start",
        "video_chapters",
        ["video_asset_id", "language_code", "start_sec"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_video_chapters_video_asset_id_language_start", table_name="video_chapters")
    op.drop_index("ix_video_chapters_video_asset_id_language_index", table_name="video_chapters")
    op.drop_table("video_chapters")

