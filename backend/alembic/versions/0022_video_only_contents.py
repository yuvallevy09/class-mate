"""Simplify to video-only course contents

Revision ID: 0022_video_only_contents
Revises: 0021_video_asset_ai_desc
Create Date: 2026-06-11

v1 pivot: lecture-video-only teaching assistant.
DESTRUCTIVE: deletes non-media course_contents rows (cascades to
content_chunks/document_pages), drops document_pages, narrows the
category CHECK to 'media', and drops the PDF-era ingestion_* columns.
"""

from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0022_video_only_contents"
down_revision = "0021_video_asset_ai_desc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove non-media content rows; FKs cascade to content_chunks and
    # document_pages. video_assets only ever link to media rows, so unaffected.
    op.execute("DELETE FROM course_contents WHERE category <> 'media'")
    # Safety net for any orphaned non-media chunks.
    op.execute("DELETE FROM content_chunks WHERE category <> 'media'")

    # Drop document_pages (PDF/PPTX page text; feature removed).
    op.drop_index("ix_document_pages_content_id", table_name="document_pages")
    op.drop_index("ix_document_pages_course_id", table_name="document_pages")
    op.drop_table("document_pages")

    # Narrow the category CHECK constraint to media-only.
    op.drop_constraint("ck_course_contents_category", "course_contents", type_="check")
    op.create_check_constraint(
        "ck_course_contents_category",
        "course_contents",
        "category IN ('media')",
    )

    # Drop ingestion lifecycle columns: only the removed PDF/PPTX pipeline
    # used them; videos track their lifecycle on video_assets.status.
    op.drop_index("ix_course_contents_ingestion_status", table_name="course_contents")
    op.drop_column("course_contents", "ingestion_completed_at")
    op.drop_column("course_contents", "ingestion_started_at")
    op.drop_column("course_contents", "ingestion_error")
    op.drop_column("course_contents", "ingestion_warning")
    op.drop_column("course_contents", "ingestion_status")


def downgrade() -> None:
    raise NotImplementedError(
        "0022 is a destructive simplification (deleted rows and dropped tables/columns). "
        "Restore from a database backup instead of downgrading."
    )
