from __future__ import annotations

from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content_chunk import ContentChunk
from app.rag.types import RagHit


async def retrieve_course_chunk_hits(
    *,
    db: AsyncSession,
    course_id: UUID,
    query: str,
    top_k: int = 8,
    categories: Sequence[str] | None = None,
) -> list[RagHit]:
    """
    Postgres FTS retrieval over `content_chunks`.

    - Scopes to a single course_id (your chats are course-scoped)
    - Optionally filters to router-selected category(ies)
    - Uses websearch_to_tsquery('simple', q) for language-agnostic parsing
    - Ranks via ts_rank_cd
    """
    q = (query or "").strip()
    if not q:
        return []

    k = int(top_k)
    if k <= 0:
        return []

    tsquery = func.websearch_to_tsquery("simple", q)
    rank = func.ts_rank_cd(ContentChunk.tsv, tsquery).label("rank")

    stmt = select(ContentChunk, rank).where(
        ContentChunk.course_id == course_id,
        ContentChunk.tsv.op("@@")(tsquery),
    )

    if categories:
        cats = [str(c).strip() for c in categories if str(c).strip()]
        if cats:
            stmt = stmt.where(ContentChunk.category.in_(cats))

    stmt = stmt.order_by(rank.desc(), ContentChunk.created_at.desc()).limit(k)

    res = await db.execute(stmt)
    out: list[RagHit] = []
    for chunk, score in res.all():
        meta = dict(chunk.meta or {})
        # Normalize a few common fields so the citation layer has what it needs.
        meta.setdefault("content_id", str(chunk.content_id))
        meta.setdefault("course_id", str(chunk.course_id))
        meta.setdefault("category", str(chunk.category))
        out.append(RagHit(text=str(chunk.text or ""), metadata=meta, score=float(score or 0.0)))
    return out


