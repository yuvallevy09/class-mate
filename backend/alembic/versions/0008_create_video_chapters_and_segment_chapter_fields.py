"""Create video_chapters table + chapter fields on transcript_segments

Revision ID: 0008_video_chapters
Revises: 0007_video_assets_segments
Create Date: 2025-12-21

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0008_video_chapters"
down_revision = "0007_video_assets_segments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_chapters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("video_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_sec", sa.Float(), nullable=False),
        sa.Column("end_sec", sa.Float(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
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
        ),
        sa.ForeignKeyConstraint(["video_asset_id"], ["video_assets.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_video_chapters_video_asset_id", "video_chapters", ["video_asset_id"], unique=False)
    op.create_index(
        "ix_video_chapters_video_asset_id_start_sec",
        "video_chapters",
        ["video_asset_id", "start_sec"],
        unique=False,
    )

    # transcript_segments: add chapter references + denormalized title for easy metadata/citations
    op.add_column("transcript_segments", sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("transcript_segments", sa.Column("chapter_title", sa.Text(), nullable=True))
    op.create_index("ix_transcript_segments_chapter_id", "transcript_segments", ["chapter_id"], unique=False)
    op.create_foreign_key(
        "fk_transcript_segments_chapter_id",
        "transcript_segments",
        "video_chapters",
        ["chapter_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_transcript_segments_chapter_id", "transcript_segments", type_="foreignkey")
    op.drop_index("ix_transcript_segments_chapter_id", table_name="transcript_segments")
    op.drop_column("transcript_segments", "chapter_title")
    op.drop_column("transcript_segments", "chapter_id")

    op.drop_index("ix_video_chapters_video_asset_id_start_sec", table_name="video_chapters")
    op.drop_index("ix_video_chapters_video_asset_id", table_name="video_chapters")
    op.drop_table("video_chapters")




