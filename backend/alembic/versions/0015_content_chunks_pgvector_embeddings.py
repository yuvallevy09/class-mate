"""Add pgvector embeddings to content_chunks

Revision ID: 0015_content_chunks_pgvector
Revises: 0014_content_ingestion
Create Date: 2026-01-09

- Enables pgvector extension (vector)
- Adds nullable embedding column to content_chunks
- Adds HNSW index for cosine similarity
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0015_content_chunks_pgvector"
down_revision = "0014_content_ingestion"
branch_labels = None
depends_on = None


class Vector(sa.types.UserDefinedType):
    """Minimal pgvector SQLAlchemy type for migrations (no runtime dependency)."""

    cache_ok = True

    def __init__(self, dims: int) -> None:
        self.dims = int(dims)

    def get_col_spec(self, **kw) -> str:  # noqa: D401
        return f"vector({self.dims})"


def upgrade() -> None:
    # Ensure pgvector exists before adding columns/indexes that reference it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # NOTE: This is pinned to the embedding dimension you plan to use.
    # If you switch embedding models later, you'll need a migration.
    dims = 1536
    op.add_column("content_chunks", sa.Column("embedding", Vector(dims), nullable=True))

    # HNSW cosine index (best default for dev ergonomics / fast top-k queries).
    op.execute(
        "CREATE INDEX IF NOT EXISTS content_chunks_embedding_hnsw_idx "
        "ON content_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS content_chunks_embedding_hnsw_idx")
    op.drop_column("content_chunks", "embedding")


