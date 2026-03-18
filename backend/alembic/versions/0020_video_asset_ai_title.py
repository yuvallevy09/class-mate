"""Add AI title fields to video_assets

Revision ID: 0020_video_asset_ai_title
Revises: 0019_video_chunk_chapter
Create Date: 2026-02-02

Stores a generated, persistent AI title for each uploaded video asset so the frontend
can display the same title on every visit without re-calling the LLM.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0020_video_asset_ai_title"
down_revision = "0019_video_chunk_chapter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_assets", sa.Column("ai_title", sa.String(length=255), nullable=True))
    op.add_column("video_assets", sa.Column("ai_title_generated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("video_assets", sa.Column("ai_title_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("video_assets", "ai_title_error")
    op.drop_column("video_assets", "ai_title_generated_at")
    op.drop_column("video_assets", "ai_title")

