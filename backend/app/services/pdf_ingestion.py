from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from uuid import UUID

import boto3
from pypdf import PdfReader
from sqlalchemy import delete, select

from app.core.settings import Settings, get_settings
from app.db.models.content_chunk import ContentChunk
from app.db.models.course_content import CourseContent
from app.db.models.document_page import DocumentPage
from app.db.session import get_session_maker


@dataclass(frozen=True)
class ExtractedPage:
    page_no: int
    text: str


def _s3_client(settings: Settings):
    kwargs: dict[str, Any] = {"service_name": "s3", "region_name": settings.s3_region}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client(**kwargs)


def _is_pdf(content: CourseContent) -> bool:
    mt = (content.mime_type or "").lower().strip()
    if "pdf" in mt:
        return True
    name = (content.original_filename or "").lower().strip()
    return name.endswith(".pdf")


def _extract_pdf_pages(pdf_bytes: bytes) -> list[ExtractedPage]:
    """
    Extract page-level text from a typed PDF.

    For scanned PDFs, this will often return empty/near-empty output; in the future,
    we can detect that and route to an OCR pipeline.
    """
    reader = PdfReader(BytesIO(pdf_bytes))
    out: list[ExtractedPage] = []
    for idx, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = (text or "").strip()
        out.append(ExtractedPage(page_no=idx + 1, text=text))
    return out


def _text_splitter(settings: Settings):
    # Character-based splitter is robust across PDFs and avoids tokenizer dependencies.
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    chunk_size = int(getattr(settings, "rag_chunk_size", 1200))
    overlap = int(getattr(settings, "rag_chunk_overlap", 200))
    return RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=overlap)


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


async def ingest_pdf_content_to_db(*, content_id: UUID) -> None:
    """
    Ingest a single file-backed course content item into the Postgres retrieval layer:
    - document_pages: one row per page (for debugging + citations)
    - content_chunks: page-scoped chunks with metadata.page_start/page_end for citations

    This is idempotent: it deletes and rebuilds pages/chunks for the given content_id.
    """
    settings = get_settings()
    if not getattr(settings, "rag_enabled", True):
        return
    if not settings.s3_bucket:
        return

    SessionLocal = get_session_maker()

    # Load content record.
    async with SessionLocal() as db:
        res = await db.execute(select(CourseContent).where(CourseContent.id == content_id))
        content = res.scalar_one_or_none()
        if content is None:
            return
        if not content.file_key:
            return
        if not _is_pdf(content):
            return

        # Mark processing (best-effort status tracking).
        content.ingestion_status = "processing"
        content.ingestion_warning = None
        content.ingestion_error = None
        content.ingestion_started_at = datetime.now(timezone.utc)
        content.ingestion_completed_at = None
        await db.commit()

        course_id = content.course_id
        category = content.category
        file_key = content.file_key
        original_filename = content.original_filename

    # Download bytes from S3 outside DB transaction.
    s3 = _s3_client(settings)
    try:
        obj = s3.get_object(Bucket=settings.s3_bucket, Key=file_key)
        body = obj.get("Body")
        pdf_bytes = body.read() if body is not None else b""
    except Exception as e:
        async with SessionLocal() as db:
            res = await db.execute(select(CourseContent).where(CourseContent.id == content_id))
            c = res.scalar_one_or_none()
            if c is not None:
                c.ingestion_status = "error"
                c.ingestion_error = str(e)
                c.ingestion_completed_at = datetime.now(timezone.utc)
                await db.commit()
        return

    pages = _extract_pdf_pages(pdf_bytes)
    if not pages:
        async with SessionLocal() as db:
            res = await db.execute(select(CourseContent).where(CourseContent.id == content_id))
            c = res.scalar_one_or_none()
            if c is not None:
                c.ingestion_status = "warning"
                c.ingestion_warning = "no_pages_extracted"
                c.ingestion_completed_at = datetime.now(timezone.utc)
                await db.commit()
        return

    splitter = _text_splitter(settings)

    # Build (pages, chunks) in memory first so the DB transaction is short.
    page_rows: list[DocumentPage] = []
    chunk_rows: list[ContentChunk] = []
    chunk_index = 0

    for p in pages:
        txt = (p.text or "").strip()
        page_rows.append(
            DocumentPage(
                course_id=course_id,
                content_id=content_id,
                page_no=int(p.page_no),
                text=txt,
                text_sha256=_sha256_hex(txt),
            )
        )

        if not txt:
            continue

        # Chunk within-page to keep citations accurate (page_start=end=page_no).
        chunks = splitter.split_text(txt)
        for c in chunks:
            c = (c or "").strip()
            if not c:
                continue
            chunk_rows.append(
                ContentChunk(
                    course_id=course_id,
                    content_id=content_id,
                    category=category,
                    chunk_index=chunk_index,
                    text=c,
                    meta={
                        "doc_type": "pdf",
                        "page_start": int(p.page_no),
                        "page_end": int(p.page_no),
                        "original_filename": original_filename,
                    },
                )
            )
            chunk_index += 1

    async with SessionLocal() as db:
        # Replace-all semantics for this content_id.
        await db.execute(delete(DocumentPage).where(DocumentPage.content_id == content_id))
        await db.execute(delete(ContentChunk).where(ContentChunk.content_id == content_id))

        if page_rows:
            db.add_all(page_rows)
        if chunk_rows:
            db.add_all(chunk_rows)

        # Mark done/warning depending on extracted text volume.
        total_chars = sum(len((r.text or "").strip()) for r in page_rows)
        res = await db.execute(select(CourseContent).where(CourseContent.id == content_id))
        c = res.scalar_one_or_none()
        if c is not None:
            if total_chars < 200:
                c.ingestion_status = "warning"
                c.ingestion_warning = "low_text_extracted"
            else:
                c.ingestion_status = "done"
                c.ingestion_warning = None
            c.ingestion_error = None
            c.ingestion_completed_at = datetime.now(timezone.utc)
        await db.commit()


