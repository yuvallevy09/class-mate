from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ContentChunk(Base):
    """
    Unified retrieval chunk for course-scoped RAG.

    This table is intended to be the single source of truth for retrieval:
    - filter by course_id (always)
    - filter by category (router-selected types)
    - search via BM25 ranking (pg_textsearch)
    - later: add pgvector embeddings (nullable column + backfill)
    """

    __tablename__ = "content_chunks"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("course_contents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    category: Mapped[str] = mapped_column(String(64), nullable=False, index=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # NOTE: attribute name cannot be `metadata` (reserved by SQLAlchemy Declarative API).
    meta: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=sa_text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


