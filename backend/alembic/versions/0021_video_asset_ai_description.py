"""Add AI description fields to video_assets

Revision ID: 0021_video_asset_ai_desc
Revises: 0020_video_asset_ai_title
Create Date: 2026-06-09

Stores a generated, persistent AI description (1–3 sentences) for each uploaded
video asset alongside the existing AI title and summary, so the frontend can show
a stable blurb without re-calling the LLM.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0021_video_asset_ai_desc"
down_revision = "0020_video_asset_ai_title"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_assets", sa.Column("ai_description", sa.Text(), nullable=True))
    op.add_column("video_assets", sa.Column("ai_description_generated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("video_assets", sa.Column("ai_description_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("video_assets", "ai_description_error")
    op.drop_column("video_assets", "ai_description_generated_at")
    op.drop_column("video_assets", "ai_description")
