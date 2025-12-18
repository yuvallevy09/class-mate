from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
import shutil

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.settings import Settings, get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.user import User
from app.db.session import get_db
from app.rag.ingest import index_course_for_user
from app.rag.paths import course_persist_dir
from app.rag.retrieve import retrieve_course_hits

router = APIRouter(tags=["rag"])


class RagStatusResponse(BaseModel):
    rag_enabled: bool
    embeddings_configured: bool
    embeddings_provider: str
    embeddings_model: str | None
    s3_configured: bool
    index_dir: str
    index_exists: bool
    index_last_modified_at: datetime | None
    course_file_count: int
    course_pdf_count: int


class RagQueryResponse(BaseModel):
    hits: list[dict]


async def _ensure_owned_course(db: AsyncSession, *, course_id: UUID, user_id: int) -> Course:
    res = await db.execute(select(Course).where(Course.id == course_id, Course.user_id == user_id))
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


def _embeddings_configured(settings: Settings) -> bool:
    provider = (getattr(settings, "rag_embeddings_provider", "gemini") or "gemini").strip().lower()

    if provider == "hf":
        try:
            from langchain_huggingface import HuggingFaceEmbeddings  # noqa: F401
        except Exception:
            return False
        return True

    # Default: Gemini
    api_key = (settings.google_api_key or "").strip() or (settings.gemini_api_key or "").strip()
    if not api_key:
        return False
    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings  # noqa: F401
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

    # Count file-backed contents (and PDFs) for a quick sanity check.
    res = await db.execute(
        select(CourseContent).where(CourseContent.course_id == course_id, CourseContent.file_key.is_not(None))
    )
    contents = list(res.scalars().all())
    file_count = len(contents)
    pdf_count = 0
    for c in contents:
        mt = (c.mime_type or "").lower().strip()
        name = (c.original_filename or "").lower().strip()
        if ("pdf" in mt) or name.endswith(".pdf"):
            pdf_count += 1

    persist_dir = course_persist_dir(
        rag_store_dir=settings.rag_store_dir, user_id=int(current_user.id), course_id=course_id
    )
    exists = persist_dir.exists()
    last_modified: datetime | None = None
    if exists:
        try:
            ts = persist_dir.stat().st_mtime
            last_modified = datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            last_modified = None

    return RagStatusResponse(
        rag_enabled=bool(settings.rag_enabled),
        embeddings_configured=_embeddings_configured(settings),
        embeddings_provider=str(getattr(settings, "rag_embeddings_provider", "gemini")),
        embeddings_model=(
            str(getattr(settings, "rag_local_embedding_model", None))
            if str(getattr(settings, "rag_embeddings_provider", "gemini")).strip().lower() == "hf"
            else str(getattr(settings, "rag_embedding_model", None))
        ),
        s3_configured=bool(settings.s3_bucket),
        index_dir=str(persist_dir),
        index_exists=exists,
        index_last_modified_at=last_modified,
        course_file_count=file_count,
        course_pdf_count=pdf_count,
    )


@router.post("/courses/{course_id}/rag/reindex")
async def rag_reindex(
    course_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    if not settings.rag_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="RAG is disabled (RAG_ENABLED=false)")
    if not settings.s3_bucket:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="S3 is not configured (missing S3_BUCKET); cannot reindex",
        )

    background_tasks.add_task(index_course_for_user, user_id=int(current_user.id), course_id=course_id)
    return {"ok": True}


@router.post("/courses/{course_id}/rag/clear")
async def rag_clear(
    course_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Dev-only helper: delete the persisted on-disk index for this course.
    Useful when switching embedding models/providers (vectors are not compatible).
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    persist_dir = course_persist_dir(
        rag_store_dir=settings.rag_store_dir, user_id=int(current_user.id), course_id=course_id
    )
    if persist_dir.exists():
        try:
            shutil.rmtree(persist_dir)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to clear index") from e
    return {"ok": True}


@router.get("/courses/{course_id}/rag/query", response_model=RagQueryResponse)
async def rag_query(
    course_id: UUID,
    q: str,
    top_k: int = 4,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> RagQueryResponse:
    """
    Debug endpoint: run retrieval only (no LLM) to verify the vector index is populated
    and relevant to a query.
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    if not settings.rag_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="RAG is disabled (RAG_ENABLED=false)")
    if not q or not q.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="q is required")

    try:
        hits = retrieve_course_hits(
            settings=settings,
            user_id=int(current_user.id),
            course_id=course_id,
            query=q.strip(),
            top_k=int(top_k),
        )
    except Exception as e:
        # Common failure mode in local dev: embeddings provider rejects requests due to quota/billing.
        msg = str(e)
        if "ResourceExhausted" in msg or "Quota exceeded" in msg or "429" in msg:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Embeddings request failed due to quota/billing limits. "
                    "Your RAG index may exist on disk, but retrieval requires embedding the query. "
                    "Enable Gemini embeddings quota/billing for your API key (or switch to a local embeddings provider)."
                ),
            ) from e
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="RAG retrieval failed") from e

    # Return a safe, JSON-friendly view (avoid leaking huge payloads).
    out: list[dict] = []
    for h in hits:
        meta = dict(h.metadata or {})
        out.append(
            {
                "text": (h.text or "")[:800],
                "score": h.score,
                "metadata": {
                    "content_id": meta.get("content_id"),
                    "title": meta.get("title"),
                    "original_filename": meta.get("original_filename"),
                    "page": meta.get("page"),
                },
            }
        )

    return RagQueryResponse(hits=out)


