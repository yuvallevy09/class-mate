"""Add audio_file_key to video_assets

Revision ID: 0009_add_video_assets_audio_key
Revises: 0008_recreate_video_assets_v2
Create Date: 2026-01-05

Stores the extracted audio artifact key (uploaded back to S3) so:
- retries are easier (no need to re-extract every time)
- debugging is easier (you can presign and inspect the audio)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0009_add_video_assets_audio_key"
down_revision = "0008_recreate_video_assets_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_assets", sa.Column("audio_file_key", sa.String(length=1024), nullable=True))
    op.create_index("ix_video_assets_audio_file_key", "video_assets", ["audio_file_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_video_assets_audio_file_key", table_name="video_assets")
    op.drop_column("video_assets", "audio_file_key")


