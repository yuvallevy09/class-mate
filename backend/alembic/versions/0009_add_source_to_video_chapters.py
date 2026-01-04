"""Add source field to video_chapters

Revision ID: 0009_video_chapters_source
Revises: 0008_video_chapters
Create Date: 2025-12-21

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0009_video_chapters_source"
down_revision = "0008_video_chapters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_chapters",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
    )
    op.create_index("ix_video_chapters_source", "video_chapters", ["source"], unique=False)
    # Remove server default to keep schema clean (app provides defaults).
    op.alter_column("video_chapters", "source", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_video_chapters_source", table_name="video_chapters")
    op.drop_column("video_chapters", "source")




