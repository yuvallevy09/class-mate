"""Add thumbnail_file_key to video_assets

Revision ID: 0023_video_asset_thumbnail
Revises: 0022_video_only_contents
Create Date: 2026-06-11

Stores the S3 key of a JPEG thumbnail extracted from the video (one frame near
the start), so the video library UI can show real thumbnails instead of icons.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0023_video_asset_thumbnail"
down_revision = "0022_video_only_contents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_assets", sa.Column("thumbnail_file_key", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("video_assets", "thumbnail_file_key")
