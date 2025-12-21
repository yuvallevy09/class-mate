from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

import boto3
from pypdf import PdfReader
from sqlalchemy import select

from app.core.settings import Settings, get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.session import get_session_maker
from app.rag.chroma import get_embeddings, load_chroma
from app.rag.paths import course_persist_dir


def _s3_client(settings: Settings):
    kwargs: dict[str, Any] = {"service_name": "s3", "region_name": settings.s3_region}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client(**kwargs)


def _text_splitter(settings: Settings):
    # Character-based splitter is robust across PDFs and avoids tokenizer dependencies.
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    chunk_size = int(getattr(settings, "rag_chunk_size", 1200))
    overlap = int(getattr(settings, "rag_chunk_overlap", 200))
    return RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=overlap)


def _is_pdf(content: CourseContent) -> bool:
    mt = (content.mime_type or "").lower().strip()
    if "pdf" in mt:
        return True
    name = (content.original_filename or "").lower().strip()
    return name.endswith(".pdf")


def _extract_pdf_pages(pdf_bytes: bytes) -> list[tuple[int, str]]:
    reader = PdfReader(BytesIO(pdf_bytes))
    out: list[tuple[int, str]] = []
    for idx, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.strip()
        if text:
            out.append((idx + 1, text))
    return out


async def index_course_for_user(*, user_id: int, course_id: UUID) -> None:
    """
    Build/refresh a per-course persisted Chroma index from all PDF contents for the course.

    For demo simplicity, this rebuilds/upserts chunk IDs deterministically per content_id.
    """
    settings = get_settings()
    if not getattr(settings, "rag_enabled", True):
        return
    if not settings.s3_bucket:
        # Nothing to index without storage.
        return

    # If embeddings aren't configured, skip indexing (chat will still fallback).
    try:
        get_embeddings(settings)
    except ValueError:
        return

    SessionLocal = get_session_maker()
    async with SessionLocal() as db:
        # Ownership: ensure course exists and belongs to this user.
        res = await db.execute(select(Course).where(Course.id == course_id, Course.user_id == user_id))
        course = res.scalar_one_or_none()
        if course is None:
            return

        res = await db.execute(
            select(CourseContent).where(
                CourseContent.course_id == course_id,
                CourseContent.file_key.is_not(None),
            )
        )
        contents = list(res.scalars().all())

    pdf_contents = [c for c in contents if c.file_key and _is_pdf(c)]
    if not pdf_contents:
        return

    persist_dir = course_persist_dir(rag_store_dir=settings.rag_store_dir, user_id=user_id, course_id=course_id)
    persist_dir.mkdir(parents=True, exist_ok=True)

    store = load_chroma(
        persist_dir=persist_dir,
        settings=settings,
        collection_name=f"classmate_course_{course_id}",
    )
    splitter = _text_splitter(settings)

    s3 = _s3_client(settings)

    # Upsert chunks with stable IDs to reduce duplication across re-index runs.
    for content in pdf_contents:
        try:
            obj = s3.get_object(Bucket=settings.s3_bucket, Key=content.file_key)
            body = obj.get("Body")
            pdf_bytes = body.read() if body is not None else b""
        except Exception:
            continue

        pages = _extract_pdf_pages(pdf_bytes)
        if not pages:
            continue

        # Build per-page documents so we can cite page numbers.
        docs = []
        for page_num, text in pages:
            docs.append(
                {
                    "page_content": text,
                    "metadata": {
                        "content_id": str(content.id),
                        "title": content.title,
                        "original_filename": content.original_filename,
                        "page": page_num,
                    },
                }
            )

        # Convert to LangChain Documents and split.
        from langchain_core.documents import Document

        page_docs = [Document(page_content=d["page_content"], metadata=d["metadata"]) for d in docs]
        chunks = splitter.split_documents(page_docs)

        # Assign stable IDs.
        ids = [f"{content.id}:{i}" for i in range(len(chunks))]

        try:
            store.add_documents(chunks, ids=ids)
        except Exception:
            # For demo robustness, ignore per-document failures.
            continue

    # Chroma auto-persists in modern versions; no manual persist() needed.


