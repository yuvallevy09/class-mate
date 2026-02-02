"""Add AI summary fields to video_assets

Revision ID: 0016_video_asset_ai_summary
Revises: 0015_content_chunks_pgvector
Create Date: 2026-02-02

Stores a generated, persistent AI summary for each uploaded video asset so the frontend
can display the same summary on every visit without re-calling the LLM.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0016_video_asset_ai_summary"
down_revision = "0015_content_chunks_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_assets", sa.Column("ai_summary", sa.Text(), nullable=True))
    op.add_column("video_assets", sa.Column("ai_summary_generated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("video_assets", sa.Column("ai_summary_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("video_assets", "ai_summary_error")
    op.drop_column("video_assets", "ai_summary_generated_at")
    op.drop_column("video_assets", "ai_summary")

