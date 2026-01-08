from __future__ import annotations

from uuid import UUID

from app.core.settings import get_settings
from app.db.models.course_content import CourseContent
from app.db.session import get_session_maker
from sqlalchemy import select

from app.services.pdf_ingestion import ingest_pdf_content_to_db
from app.services.pptx_ingestion import ingest_pptx_content_to_db


def _is_pptx_like(content: CourseContent) -> bool:
    mt = (content.mime_type or "").lower().strip()
    name = (content.original_filename or "").lower().strip()
    if "presentation" in mt or "powerpoint" in mt:
        return True
    return name.endswith(".pptx")


def _is_pdf_like(content: CourseContent) -> bool:
    mt = (content.mime_type or "").lower().strip()
    name = (content.original_filename or "").lower().strip()
    if "pdf" in mt:
        return True
    return name.endswith(".pdf")


async def ingest_content_to_db(*, content_id: UUID) -> None:
    """
    Dispatcher: ingest a file-backed course content item into Postgres retrieval tables.

    - PDFs: document_pages + content_chunks (doc_type=pdf)
    - PPTX: document_pages + content_chunks (doc_type=slides, source_kind=pptx)
    """
    settings = get_settings()
    if not getattr(settings, "rag_enabled", True):
        return

    SessionLocal = get_session_maker()
    async with SessionLocal() as db:
        res = await db.execute(select(CourseContent).where(CourseContent.id == content_id))
        content = res.scalar_one_or_none()
        if content is None or not content.file_key:
            return

    # Decide based on mime/extension.
    if _is_pptx_like(content):
        await ingest_pptx_content_to_db(content_id=content_id)
    elif _is_pdf_like(content):
        await ingest_pdf_content_to_db(content_id=content_id)
    else:
        # Unsupported file type for ingestion; leave status untouched.
        return


