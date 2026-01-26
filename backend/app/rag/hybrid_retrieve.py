from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence
from uuid import UUID

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.rag.embeddings import get_embeddings
from app.rag.embedding_config import EMBEDDING_DIMS
from app.rag.pg_retrieve import retrieve_course_chunk_hits
from app.rag.types import RagHit


@dataclass(frozen=True)
class HybridRetrieveConfig:
    lexical_k: int = 20
    semantic_k: int = 20
    top_k: int = 8
    rrf_k0: int = 60


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.9g}" for x in vec) + "]"


def _rrf_merge(
    *,
    lexical: Sequence[RagHit],
    semantic: Sequence[RagHit],
    k0: int,
    top_k: int,
) -> list[RagHit]:
    # Use a stable key so we can dedupe across retrieval sources.
    def key(h: RagHit) -> str:
        m = h.metadata or {}
        # Prefer content-chunk id if present; otherwise fallback to content_id+snippet.
        return str(m.get("chunk_id") or m.get("id") or (str(m.get("content_id")) + "|" + (h.text or "")[:120]))

    merged: dict[str, dict[str, Any]] = {}

    def add(list_name: str, hits: Sequence[RagHit]):
        for rank, h in enumerate(hits, start=1):
            k = key(h)
            if k not in merged:
                merged[k] = {"hit": h, "rrf": 0.0, "sources": set()}
            merged[k]["rrf"] += 1.0 / (float(k0) + float(rank))
            merged[k]["sources"].add(list_name)

    add("lexical", lexical)
    add("semantic", semantic)

    items = list(merged.values())
    items.sort(key=lambda x: (float(x["rrf"]),), reverse=True)

    out: list[RagHit] = []
    for it in items[: max(0, int(top_k))]:
        h: RagHit = it["hit"]
        m = dict(h.metadata or {})
        # Attach debug info to help tune RRF later.
        m.setdefault("rrf", float(it["rrf"]))
        m.setdefault("sources", sorted(list(it["sources"])))
        out.append(RagHit(text=h.text, metadata=m, score=float(it["rrf"])))
    return out


async def _semantic_top_k(
    *,
    db: AsyncSession,
    course_id: UUID,
    query: str,
    top_k: int,
    categories: Sequence[str] | None,
) -> list[RagHit]:
    settings: Settings = get_settings()

    # Avoid external embedding calls if this course has no stored embeddings.
    exists_res = await db.execute(
        sa_text(
            "SELECT 1 FROM content_chunks WHERE course_id = :course_id AND embedding IS NOT NULL LIMIT 1"
        ),
        {"course_id": str(course_id)},
    )
    if exists_res.first() is None:
        return []

    emb = get_embeddings(settings)
    qvec = emb.embed_query((query or "").strip())
    if not isinstance(qvec, list) or len(qvec) != EMBEDDING_DIMS:
        return []

    qlit = _vector_literal(qvec)
    k = int(top_k)
    if k <= 0:
        return []

    cats = [str(c).strip() for c in (categories or []) if str(c).strip()]

    sql = """
    SELECT
      id,
      content_id,
      course_id,
      category,
      text,
      metadata,
      created_at,
      (embedding <=> :qvec::vector) AS distance
    FROM content_chunks
    WHERE course_id = :course_id
      AND embedding IS NOT NULL
    """
    if cats:
        sql += " AND category = ANY(:cats)\n"
    sql += " ORDER BY (embedding <=> :qvec::vector) ASC, created_at DESC LIMIT :k"

    params: dict[str, Any] = {"course_id": str(course_id), "qvec": qlit, "k": k}
    if cats:
        params["cats"] = cats

    res = await db.execute(sa_text(sql), params)
    rows = res.mappings().all()

    out: list[RagHit] = []
    for r in rows:
        meta = dict(r.get("metadata") or {})
        # Normalize fields for citation layer + stable merging.
        meta.setdefault("chunk_id", str(r.get("id")))
        meta.setdefault("content_id", str(r.get("content_id")))
        meta.setdefault("course_id", str(r.get("course_id")))
        meta.setdefault("category", str(r.get("category")))
        dist = float(r.get("distance") or 0.0)
        # Return semantic score as similarity (higher is better) to keep it intuitive.
        out.append(RagHit(text=str(r.get("text") or ""), metadata=meta, score=1.0 - dist))
    return out


async def retrieve_course_hybrid_hits(
    *,
    db: AsyncSession,
    course_id: UUID,
    query: str,
    cfg: HybridRetrieveConfig | None = None,
    categories: Iterable[str] | None = None,
) -> list[RagHit]:
    """
    Hybrid retrieval over Postgres retrieval corpus (`content_chunks`):
    - Lexical: pg_textsearch BM25
    - Semantic: pgvector cosine distance
    - Merge: Reciprocal Rank Fusion (RRF)

    Safe defaults:
    - If embeddings are not configured / query embed fails, returns lexical-only.
    """
    cfg = cfg or HybridRetrieveConfig()
    q = (query or "").strip()
    if not q:
        return []

    cats = [str(c).strip() for c in (categories or []) if str(c).strip()] if categories else None

    lexical = await retrieve_course_chunk_hits(
        db=db, course_id=course_id, query=q, top_k=int(cfg.lexical_k), categories=cats
    )

    # Best-effort semantic retrieval.
    try:
        semantic = await _semantic_top_k(
            db=db, course_id=course_id, query=q, top_k=int(cfg.semantic_k), categories=cats
        )
    except Exception:
        semantic = []

    # If semantic is unavailable, keep existing behavior (BM25 only).
    if not semantic:
        return lexical[: int(cfg.top_k)]

    return _rrf_merge(lexical=lexical, semantic=semantic, k0=int(cfg.rrf_k0), top_k=int(cfg.top_k))


