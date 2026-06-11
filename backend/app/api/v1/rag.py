from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from pydantic import BaseModel
from sqlalchemy import func, select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.settings import Settings, get_settings
from app.db.models.content_chunk import ContentChunk
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.user import User
from app.db.session import get_db
from app.rag.embeddings import get_embeddings
from app.rag.embedding_config import EMBEDDING_DIMS
from app.rag.pg_retrieve import retrieve_course_chunk_hits

router = APIRouter(tags=["rag"])


class RagStatusResponse(BaseModel):
    rag_enabled: bool
    embeddings_configured: bool
    embeddings_provider: str
    embeddings_model: str | None
    s3_configured: bool
    course_file_count: int
    chunks_total: int
    chunks_with_embeddings: int


class RagQueryResponse(BaseModel):
    hits: list[dict]


class RagLexicalQueryResponse(BaseModel):
    hits: list[dict]

class RagSemanticQueryResponse(BaseModel):
    hits: list[dict]

async def _ensure_owned_course(db: AsyncSession, *, course_id: UUID, user_id: int) -> Course:
    res = await db.execute(select(Course).where(Course.id == course_id, Course.user_id == user_id))
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


def _embeddings_configured(settings: Settings) -> bool:
    api_key = (getattr(settings, "openai_api_key", None) or "").strip()
    if not api_key:
        return False
    try:
        from langchain_openai import OpenAIEmbeddings  # noqa: F401
    except Exception:
        return False
    return True


@router.get("/courses/{course_id}/rag/status", response_model=RagStatusResponse)
async def rag_status(
    course_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> RagStatusResponse:
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    # Count file-backed contents for a quick sanity check.
    res = await db.execute(
        select(CourseContent).where(CourseContent.course_id == course_id, CourseContent.file_key.is_not(None))
    )
    file_count = len(list(res.scalars().all()))

    # Chunk stats (retrieval corpus lives in Postgres now).
    cres = await db.execute(
        select(func.count(ContentChunk.id)).where(ContentChunk.course_id == course_id)
    )
    chunks_total = int(cres.scalar() or 0)
    eres = await db.execute(
        select(func.count(ContentChunk.id)).where(
            ContentChunk.course_id == course_id, ContentChunk.embedding.is_not(None)
        )
    )
    chunks_with_embeddings = int(eres.scalar() or 0)

    return RagStatusResponse(
        rag_enabled=bool(settings.rag_enabled),
        embeddings_configured=_embeddings_configured(settings),
        embeddings_provider="openai",
        embeddings_model=str(getattr(settings, "rag_embedding_model", None)),
        s3_configured=bool(settings.s3_bucket),
        course_file_count=file_count,
        chunks_total=chunks_total,
        chunks_with_embeddings=chunks_with_embeddings,
    )

@router.get("/courses/{course_id}/rag/query", response_model=RagQueryResponse)
async def rag_query(
    course_id: UUID,
    q: str,
    top_k: int = 4,
    doc_type: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> RagQueryResponse:
    """
    Debug endpoint: semantic retrieval only (no LLM) using pgvector cosine distance over
    `content_chunks.embedding`.
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    if not settings.rag_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="RAG is disabled (RAG_ENABLED=false)")
    if not q or not q.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="q is required")

    try:
        emb = get_embeddings(settings)
        qvec = emb.embed_query(q.strip())
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to embed query") from e

    if not isinstance(qvec, list) or len(qvec) != EMBEDDING_DIMS:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query embedding has unexpected dimension (expected {EMBEDDING_DIMS}).",
        )

    qlit = "[" + ",".join(f"{float(x):.9g}" for x in qvec) + "]"
    k = int(top_k)
    if k <= 0:
        return RagQueryResponse(hits=[])

    sql = """
    SELECT
      content_id,
      text,
      metadata,
      (embedding <=> :qvec::vector) AS distance
    FROM content_chunks
    WHERE course_id = :course_id
      AND embedding IS NOT NULL
    """
    params: dict = {"course_id": str(course_id), "qvec": qlit, "k": k}
    if doc_type and doc_type.strip():
        sql += " AND (metadata->>'doc_type') = :doc_type\n"
        params["doc_type"] = doc_type.strip()
    sql += " ORDER BY (embedding <=> :qvec::vector) ASC LIMIT :k"

    res = await db.execute(sa_text(sql), params)
    rows = res.mappings().all()

    # Return a safe, JSON-friendly view (avoid leaking huge payloads).
    out: list[dict] = []
    for r in rows:
        meta = dict(r.get("metadata") or {})
        dist = float(r.get("distance") or 0.0)
        out.append(
            {
                "text": (str(r.get("text") or ""))[:800],
                "score": 1.0 - dist,
                "metadata": {
                    "content_id": meta.get("content_id") or str(r.get("content_id")),
                    "title": meta.get("title"),
                    "original_filename": meta.get("original_filename"),
                    "page": meta.get("page"),
                    "doc_type": meta.get("doc_type"),
                    "video_asset_id": meta.get("video_asset_id"),
                    "start_sec": meta.get("start_sec"),
                    "end_sec": meta.get("end_sec"),
                    "language_code": meta.get("language_code"),
                },
            }
        )

    return RagQueryResponse(hits=out)


@router.get("/courses/{course_id}/rag/lexical_query", response_model=RagLexicalQueryResponse)
async def rag_lexical_query(
    course_id: UUID,
    q: str,
    top_k: int = 8,
    categories: list[str] | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> RagLexicalQueryResponse:
    """
    Debug endpoint: run lexical retrieval only (no LLM) using Postgres BM25 (pg_textsearch)
    over the `content_chunks` table.
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    if not settings.rag_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="RAG is disabled (RAG_ENABLED=false)")
    if not q or not q.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="q is required")

    hits = await retrieve_course_chunk_hits(
        db=db,
        course_id=course_id,
        query=q.strip(),
        top_k=int(top_k),
        categories=categories,
    )

    out: list[dict] = []
    for h in hits:
        meta = dict(h.metadata or {})
        out.append(
            {
                "text": (h.text or "")[:800],
                "score": h.score,
                "metadata": {
                    "content_id": meta.get("content_id"),
                    "course_id": meta.get("course_id"),
                    "category": meta.get("category"),
                    # Optional ingestion-provided fields (may be absent depending on source type).
                    "title": meta.get("title"),
                    "original_filename": meta.get("original_filename"),
                    "page": meta.get("page"),
                    "doc_type": meta.get("doc_type"),
                    "video_asset_id": meta.get("video_asset_id"),
                    "start_sec": meta.get("start_sec"),
                    "end_sec": meta.get("end_sec"),
                    "language_code": meta.get("language_code"),
                },
            }
        )

    return RagLexicalQueryResponse(hits=out)


