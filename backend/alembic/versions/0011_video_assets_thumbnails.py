"""Add thumbnail fields to video_assets

Revision ID: 0011_video_assets_thumbs
Revises: 0010_video_assets_local
Create Date: 2026-01-04

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0011_video_assets_thumbs"
down_revision = "0010_video_assets_local"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_assets", sa.Column("thumbnail_file_key", sa.String(length=1024), nullable=True))
    op.add_column("video_assets", sa.Column("thumbnail_mime_type", sa.String(length=64), nullable=True))
    op.add_column("video_assets", sa.Column("thumbnail_generated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_video_assets_thumbnail_file_key", "video_assets", ["thumbnail_file_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_video_assets_thumbnail_file_key", table_name="video_assets")
    op.drop_column("video_assets", "thumbnail_generated_at")
    op.drop_column("video_assets", "thumbnail_mime_type")
    op.drop_column("video_assets", "thumbnail_file_key")


