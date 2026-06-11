"""Add AI course summary columns to courses

Revision ID: 0024_course_ai_summary
Revises: 0023_video_asset_thumbnail
Create Date: 2026-06-11

Stores the course-level AI summary ("what we've learned so far"), regenerated
from the per-lecture summaries each time a new lecture finishes transcribing.
Mirrors the `ai_summary*` trio on `video_assets`.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0024_course_ai_summary"
down_revision = "0023_video_asset_thumbnail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("courses", sa.Column("ai_summary", sa.Text(), nullable=True))
    op.add_column("courses", sa.Column("ai_summary_generated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("courses", sa.Column("ai_summary_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("courses", "ai_summary_error")
    op.drop_column("courses", "ai_summary_generated_at")
    op.drop_column("courses", "ai_summary")
