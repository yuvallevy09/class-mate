from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Iterable, Sequence
from uuid import UUID

from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.db.models.content_chunk import ContentChunk
from app.rag.embeddings import get_embeddings
from app.rag.embedding_config import EMBEDDING_DIMS
from app.rag.pg_retrieve import retrieve_course_chunk_hits
from app.rag.types import RagHit

logger = logging.getLogger(__name__)
_SEMANTIC_LOG_ONCE_KEYS: set[str] = set()


def _log_semantic_once(key: str, level: str, msg: str, *args) -> None:
    """
    Log a semantic-retrieval diagnostic only once per-process per key to avoid noise.
    Keys should include course_id + reason.
    """
    if key in _SEMANTIC_LOG_ONCE_KEYS:
        return
    _SEMANTIC_LOG_ONCE_KEYS.add(key)
    fn = getattr(logger, level, logger.info)
    fn(msg, *args)


@dataclass(frozen=True)
class HybridRetrieveConfig:
    lexical_k: int = 20
    semantic_k: int = 20
    top_k: int = 8
    rrf_k0: int = 60
    neighbor_before: int = 1
    neighbor_after: int = 1
    neighbor_max_additional: int = 6


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


async def _expand_neighbors(
    *,
    db: AsyncSession,
    base_hits: Sequence[RagHit],
    before: int,
    after: int,
    max_additional: int,
) -> list[RagHit]:
    """
    Expand retrieval context by fetching neighboring chunks (±N) within the same chapter.

    This is designed to improve answer quality for long-form sources (e.g., video transcripts)
    without inflating individual chunk sizes.
    """
    b = max(0, int(before))
    a = max(0, int(after))
    cap = max(0, int(max_additional))
    if cap <= 0 or (b == 0 and a == 0) or not base_hits:
        return list(base_hits)

    # Seed keys for dedupe.
    seen_chunk_ids: set[str] = set()
    seeds: list[tuple[str, int]] = []  # (chapter_id, chunk_index_in_chapter)

    for h in base_hits:
        m = h.metadata or {}
        cid = m.get("chunk_id")
        if cid:
            seen_chunk_ids.add(str(cid))
        chap = m.get("chapter_id")
        idx = m.get("chunk_index_in_chapter")
        if chap is None or idx is None:
            continue
        try:
            seeds.append((str(chap), int(idx)))
        except Exception:
            continue

    if not seeds:
        return list(base_hits)

    # Build a small set of desired neighbor indices per chapter.
    desired: dict[str, set[int]] = {}
    for chap_id, idx in seeds:
        s = desired.setdefault(chap_id, set())
        for d in range(1, b + 1):
            s.add(idx - d)
        for d in range(1, a + 1):
            s.add(idx + d)

    # Remove negative indices.
    for chap_id in list(desired.keys()):
        desired[chap_id] = {i for i in desired[chap_id] if i >= 0}
        if not desired[chap_id]:
            desired.pop(chap_id, None)

    if not desired:
        return list(base_hits)

    # Fetch neighbors (best-effort). Use per-chapter query to keep it simple.
    neighbors: list[RagHit] = []
    for chap_id, idxs in desired.items():
        if not idxs:
            continue
        # Cap how many we ask for per chapter.
        want = sorted(list(idxs))[: max(1, cap)]
        try:
            chap_uuid = UUID(str(chap_id))
        except Exception:
            continue

        stmt = (
            select(ContentChunk)
            .where(ContentChunk.chapter_id == chap_uuid)
            .where(ContentChunk.chunk_index_in_chapter.in_(want))
            .order_by(ContentChunk.chunk_index_in_chapter.asc())
        )
        res = await db.execute(stmt)
        chunks = list(res.scalars().all())
        for chunk in chunks:
            chunk_id = str(getattr(chunk, "id", "") or "")
            if not chunk_id or chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            meta = dict(getattr(chunk, "meta", None) or {})
            meta.setdefault("chunk_id", chunk_id)
            meta.setdefault("content_id", str(getattr(chunk, "content_id", "")))
            meta.setdefault("course_id", str(getattr(chunk, "course_id", "")))
            meta.setdefault("category", str(getattr(chunk, "category", "")))
            if getattr(chunk, "video_asset_id", None) is not None:
                meta.setdefault("video_asset_id", str(chunk.video_asset_id))
            if getattr(chunk, "chapter_id", None) is not None:
                meta.setdefault("chapter_id", str(chunk.chapter_id))
            if getattr(chunk, "chunk_start_sec", None) is not None:
                meta.setdefault("start_sec", float(chunk.chunk_start_sec))  # type: ignore[arg-type]
            if getattr(chunk, "chunk_end_sec", None) is not None:
                meta.setdefault("end_sec", float(chunk.chunk_end_sec))  # type: ignore[arg-type]
            if getattr(chunk, "chunk_index_in_chapter", None) is not None:
                meta.setdefault("chunk_index_in_chapter", int(chunk.chunk_index_in_chapter))  # type: ignore[arg-type]
            meta.setdefault("neighbor", True)
            neighbors.append(RagHit(text=str(getattr(chunk, "text", "") or ""), metadata=meta, score=None))
            if len(neighbors) >= cap:
                break
        if len(neighbors) >= cap:
            break

    if not neighbors:
        return list(base_hits)

    # Preserve base ordering; append neighbors after.
    return list(base_hits) + neighbors


async def _semantic_top_k(
    *,
    db: AsyncSession,
    course_id: UUID,
    query: str,
    top_k: int,
    categories: Sequence[str] | None,
    video_asset_ids: Sequence[UUID] | None = None,
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
        _log_semantic_once(
            f"{course_id}:no_embeddings",
            "debug",
            "Semantic retrieval disabled: no stored embeddings for course_id=%s",
            str(course_id),
        )
        return []

    try:
        emb = get_embeddings(settings)
    except ValueError as e:
        _log_semantic_once(
            f"{course_id}:embeddings_unavailable:{type(e).__name__}",
            "info",
            "Semantic retrieval disabled: embeddings provider unavailable (%s) course_id=%s",
            str(e),
            str(course_id),
        )
        return []

    qvec = emb.embed_query((query or "").strip())
    if not isinstance(qvec, list) or len(qvec) != EMBEDDING_DIMS:
        _log_semantic_once(
            f"{course_id}:dim_mismatch",
            "warning",
            "Semantic retrieval disabled: embedding dim mismatch course_id=%s expected=%s got=%s",
            str(course_id),
            str(EMBEDDING_DIMS),
            str(len(qvec) if isinstance(qvec, list) else "non-list"),
        )
        return []

    qlit = _vector_literal(qvec)
    k = int(top_k)
    if k <= 0:
        return []

    cats = [str(c).strip() for c in (categories or []) if str(c).strip()]
    asset_ids = [str(a) for a in (video_asset_ids or [])]

    # Inline the query vector as a SQL literal rather than binding it as a
    # parameter. Passing it as a bound `:qvec::vector` cast under asyncpg's
    # extended/prepared protocol crashes the Postgres backend on the HNSW index
    # scan ("ResourceOwnerEnlarge called after release started"); inlining the
    # literal avoids that path. `_vector_literal` emits only digits, `.`, `-`,
    # `e`, `+`, `,`, `[`, `]` — no quotes or SQL metacharacters — so embedding it
    # inside a single-quoted `'…'::vector` literal is injection-safe.
    dist = f"(embedding <=> '{qlit}'::vector)"
    sql = f"""
    SELECT
      id,
      content_id,
      course_id,
      category,
      video_asset_id,
      chapter_id,
      chunk_start_sec,
      chunk_end_sec,
      chunk_index_in_chapter,
      text,
      metadata,
      created_at,
      {dist} AS distance
    FROM content_chunks
    WHERE course_id = :course_id
      AND embedding IS NOT NULL
    """
    if cats:
        sql += " AND category = ANY(:cats)\n"
    # Scope to specific lectures BEFORE the ORDER BY / LIMIT so the nearest-k is
    # computed within the requested lectures, not post-filtered out of a global k.
    if asset_ids:
        sql += " AND video_asset_id = ANY(CAST(:asset_ids AS uuid[]))\n"
    sql += f" ORDER BY {dist} ASC, created_at DESC LIMIT :k"

    params: dict[str, Any] = {"course_id": str(course_id), "k": k}
    if cats:
        params["cats"] = cats
    if asset_ids:
        params["asset_ids"] = asset_ids

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
        if r.get("video_asset_id") is not None:
            meta.setdefault("video_asset_id", str(r.get("video_asset_id")))
        if r.get("chapter_id") is not None:
            meta.setdefault("chapter_id", str(r.get("chapter_id")))
        if r.get("chunk_start_sec") is not None:
            meta.setdefault("start_sec", float(r.get("chunk_start_sec") or 0.0))
        if r.get("chunk_end_sec") is not None:
            meta.setdefault("end_sec", float(r.get("chunk_end_sec") or 0.0))
        if r.get("chunk_index_in_chapter") is not None:
            meta.setdefault("chunk_index_in_chapter", int(r.get("chunk_index_in_chapter") or 0))
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
    video_asset_ids: Sequence[UUID] | None = None,
) -> list[RagHit]:
    """
    Hybrid retrieval over Postgres retrieval corpus (`content_chunks`):
    - Lexical: pg_textsearch BM25
    - Semantic: pgvector cosine distance
    - Merge: Reciprocal Rank Fusion (RRF)

    Safe defaults:
    - If embeddings are not configured / query embed fails, returns lexical-only.

    When `video_asset_ids` is provided, BOTH the lexical and semantic legs filter
    to those lectures in SQL — so each leg's top-k is computed within the scoped
    set rather than post-filtered out of a global ranking (the latter can starve
    a correctly-routed lecture whose best chunks rank below other lectures').
    """
    cfg = cfg or HybridRetrieveConfig()
    q = (query or "").strip()
    if not q:
        return []

    cats = [str(c).strip() for c in (categories or []) if str(c).strip()] if categories else None
    asset_ids = list(video_asset_ids) if video_asset_ids else None

    lexical = await retrieve_course_chunk_hits(
        db=db, course_id=course_id, query=q, top_k=int(cfg.lexical_k),
        categories=cats, video_asset_ids=asset_ids,
    )

    # Best-effort semantic retrieval.
    try:
        semantic = await _semantic_top_k(
            db=db, course_id=course_id, query=q, top_k=int(cfg.semantic_k),
            categories=cats, video_asset_ids=asset_ids,
        )
    except Exception as e:
        _log_semantic_once(
            f"{course_id}:semantic_exception:{type(e).__name__}",
            "warning",
            "Semantic retrieval failed (best-effort): %s course_id=%s",
            str(e),
            str(course_id),
        )
        # A failed semantic query can leave the transaction aborted; roll back so
        # the subsequent lexical-only return / neighbor expansion runs on a clean
        # session instead of failing with InFailedSQLTransactionError. The
        # retrieval session is read-only, so this discards nothing meaningful, and
        # `lexical` is already materialized. Guard the rollback itself so a dead
        # connection can't mask the original error.
        try:
            await db.rollback()
        except Exception:
            pass
        semantic = []

    # If semantic is unavailable, keep existing behavior (BM25 only), but still expand neighbors.
    if not semantic:
        base = lexical[: int(cfg.top_k)]
        return await _expand_neighbors(
            db=db,
            base_hits=base,
            before=int(cfg.neighbor_before),
            after=int(cfg.neighbor_after),
            max_additional=int(cfg.neighbor_max_additional),
        )

    base = _rrf_merge(lexical=lexical, semantic=semantic, k0=int(cfg.rrf_k0), top_k=int(cfg.top_k))
    return await _expand_neighbors(
        db=db,
        base_hits=base,
        before=int(cfg.neighbor_before),
        after=int(cfg.neighbor_after),
        max_additional=int(cfg.neighbor_max_additional),
    )


