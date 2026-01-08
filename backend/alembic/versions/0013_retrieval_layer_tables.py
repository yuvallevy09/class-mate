"""Create retrieval-layer tables (document_pages, content_chunks) with simple FTS

Revision ID: 0013_retrieval_layer
Revises: 0012_cc_category_check
Create Date: 2026-01-08

These tables make Postgres the single source of truth for RAG retrieval.
- document_pages: per-page extracted PDF text for debugging and page-accurate citations
- content_chunks: unified chunk corpus across all content types, with generated tsvector for FTS

Embeddings (pgvector) are intentionally NOT included in this migration; add later as a nullable
column + backfill job.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0013_retrieval_layer"
down_revision = "0012_cc_category_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # document_pages: page-level extracted text (PDFs)
    op.create_table(
        "document_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_id"], ["course_contents.id"], ondelete="CASCADE"),
        sa.CheckConstraint("page_no > 0", name="ck_document_pages_page_no_positive"),
        sa.UniqueConstraint("content_id", "page_no", name="ux_document_pages_content_page_no"),
    )
    op.create_index("ix_document_pages_course_id", "document_pages", ["course_id"], unique=False)
    op.create_index("ix_document_pages_content_id", "document_pages", ["content_id"], unique=False)

    # content_chunks: unified retrieval corpus (FTS now; embeddings later)
    op.create_table(
        "content_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # Generated tsvector using SIMPLE config (language-agnostic).
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', coalesce(text, ''))", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_id"], ["course_contents.id"], ondelete="CASCADE"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_content_chunks_chunk_index_nonneg"),
        sa.UniqueConstraint("content_id", "chunk_index", name="ux_content_chunks_content_chunk_index"),
    )
    op.create_index("ix_content_chunks_course_id", "content_chunks", ["course_id"], unique=False)
    op.create_index("ix_content_chunks_content_id", "content_chunks", ["content_id"], unique=False)
    op.create_index("ix_content_chunks_course_category", "content_chunks", ["course_id", "category"], unique=False)
    op.create_index("gin_content_chunks_tsv", "content_chunks", ["tsv"], unique=False, postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("gin_content_chunks_tsv", table_name="content_chunks")
    op.drop_index("ix_content_chunks_course_category", table_name="content_chunks")
    op.drop_index("ix_content_chunks_content_id", table_name="content_chunks")
    op.drop_index("ix_content_chunks_course_id", table_name="content_chunks")
    op.drop_table("content_chunks")

    op.drop_index("ix_document_pages_content_id", table_name="document_pages")
    op.drop_index("ix_document_pages_course_id", table_name="document_pages")
    op.drop_table("document_pages")


