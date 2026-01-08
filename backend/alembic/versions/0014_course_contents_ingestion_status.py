"""Add ingestion status fields to course_contents

Revision ID: 0014_content_ingestion
Revises: 0013_retrieval_layer
Create Date: 2026-01-08

Tracks ingestion lifecycle for file-backed course contents so the UI can show:
queued -> processing -> done/warning/error.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0014_content_ingestion"
down_revision = "0013_retrieval_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_contents",
        sa.Column("ingestion_status", sa.String(length=32), nullable=False, server_default=sa.text("'none'")),
    )
    op.add_column("course_contents", sa.Column("ingestion_warning", sa.String(length=255), nullable=True))
    op.add_column("course_contents", sa.Column("ingestion_error", sa.Text(), nullable=True))
    op.add_column("course_contents", sa.Column("ingestion_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("course_contents", sa.Column("ingestion_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_course_contents_ingestion_status", "course_contents", ["ingestion_status"], unique=False)

    # Keep the server_default on upgrade so existing rows backfill to 'none', then drop it
    # so callers must set status explicitly going forward.
    op.alter_column("course_contents", "ingestion_status", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_course_contents_ingestion_status", table_name="course_contents")
    op.drop_column("course_contents", "ingestion_completed_at")
    op.drop_column("course_contents", "ingestion_started_at")
    op.drop_column("course_contents", "ingestion_error")
    op.drop_column("course_contents", "ingestion_warning")
    op.drop_column("course_contents", "ingestion_status")


