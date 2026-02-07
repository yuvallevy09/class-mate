"""Add video chunk linkage fields to content_chunks

Revision ID: 0019_video_chunk_chapter
Revises: 0018_video_chapters
Create Date: 2026-02-07

Adds optional columns for video-derived chunks so we can link retrieval chunks to
video assets and chapters with precise timestamps.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0019_video_chunk_chapter"
down_revision = "0018_video_chapters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("content_chunks", sa.Column("video_asset_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("content_chunks", sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("content_chunks", sa.Column("chunk_start_sec", sa.Float(), nullable=True))
    op.add_column("content_chunks", sa.Column("chunk_end_sec", sa.Float(), nullable=True))
    op.add_column("content_chunks", sa.Column("chunk_index_in_chapter", sa.Integer(), nullable=True))

    op.create_index("ix_content_chunks_video_asset_id", "content_chunks", ["video_asset_id"], unique=False)
    op.create_index("ix_content_chunks_chapter_id", "content_chunks", ["chapter_id"], unique=False)
    op.create_index(
        "ix_content_chunks_video_asset_id_chunk_start_sec",
        "content_chunks",
        ["video_asset_id", "chunk_start_sec"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_content_chunks_video_asset_id",
        "content_chunks",
        "video_assets",
        ["video_asset_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_content_chunks_chapter_id",
        "content_chunks",
        "video_chapters",
        ["chapter_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_content_chunks_chapter_id", "content_chunks", type_="foreignkey")
    op.drop_constraint("fk_content_chunks_video_asset_id", "content_chunks", type_="foreignkey")
    op.drop_index("ix_content_chunks_video_asset_id_chunk_start_sec", table_name="content_chunks")
    op.drop_index("ix_content_chunks_chapter_id", table_name="content_chunks")
    op.drop_index("ix_content_chunks_video_asset_id", table_name="content_chunks")
    op.drop_column("content_chunks", "chunk_index_in_chapter")
    op.drop_column("content_chunks", "chunk_end_sec")
    op.drop_column("content_chunks", "chunk_start_sec")
    op.drop_column("content_chunks", "chapter_id")
    op.drop_column("content_chunks", "video_asset_id")

