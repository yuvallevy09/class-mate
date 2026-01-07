"""Enforce video_assets.content_id invariants (NOT NULL + CASCADE + unique)

Revision ID: 0010_video_content_fk
Revises: 0009_add_video_assets_audio_key
Create Date: 2026-01-07

This locks in the "course_contents is canonical" invariant for videos:
- Every video_assets row must point at a course_contents row via content_id (NOT NULL)
- Deleting the content item should delete the video asset (and transcript_segments via cascade)
- A content item may have at most one video asset (unique content_id)

Note: prior versions allowed content_id to be NULL (and ON DELETE SET NULL). To safely
apply NOT NULL, we delete any orphaned video_assets rows with NULL content_id (and rely
on transcript_segments.video_asset_id ON DELETE CASCADE to remove dependent segments).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
# Keep this <= 32 chars (alembic_version.version_num is VARCHAR(32) by default).
revision = "0010_video_content_fk"
down_revision = "0009_add_video_assets_audio_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop any legacy/orphan rows that would violate NOT NULL.
    op.execute(sa.text("DELETE FROM video_assets WHERE content_id IS NULL"))

    # Recreate FK with ON DELETE CASCADE and enforce NOT NULL.
    with op.batch_alter_table("video_assets") as batch:
        # Default Postgres-generated name when the constraint isn't explicitly named.
        batch.drop_constraint("video_assets_content_id_fkey", type_="foreignkey")
        batch.alter_column(
            "content_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=False,
        )
        batch.create_foreign_key(
            "video_assets_content_id_fkey",
            "course_contents",
            ["content_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Enforce 1:1 mapping between course_contents and video_assets.
    op.drop_index("ix_video_assets_content_id", table_name="video_assets")
    op.create_index("ux_video_assets_content_id", "video_assets", ["content_id"], unique=True)


def downgrade() -> None:
    # Revert unique index to a plain index.
    op.drop_index("ux_video_assets_content_id", table_name="video_assets")
    op.create_index("ix_video_assets_content_id", "video_assets", ["content_id"], unique=False)

    # Revert FK to ON DELETE SET NULL and allow NULLs.
    with op.batch_alter_table("video_assets") as batch:
        batch.drop_constraint("video_assets_content_id_fkey", type_="foreignkey")
        batch.alter_column(
            "content_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=True,
        )
        batch.create_foreign_key(
            "video_assets_content_id_fkey",
            "course_contents",
            ["content_id"],
            ["id"],
            ondelete="SET NULL",
        )


