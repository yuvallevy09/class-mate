from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.content_chunk import ContentChunk
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.document_page import DocumentPage
from app.db.models.user import User
import app.services.pdf_ingestion as pdf_ingestion


async def _can_connect(database_url: str) -> bool:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


def _run_migrations_sync() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


@pytest.mark.asyncio
async def test_pdf_ingestion_writes_pages_and_chunks(monkeypatch) -> None:
    # Ensure settings allow ingestion.
    monkeypatch.setenv("S3_BUCKET", "classmate")
    monkeypatch.setenv("RAG_ENABLED", "true")
    get_settings.cache_clear()
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            user = User(email=f"u-{uuid4()}@e.com", hashed_password=hash_password("pw"), display_name="T")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            course = Course(user_id=user.id, name="Course", description=None)
            session.add(course)
            await session.commit()
            await session.refresh(course)

            content = CourseContent(
                course_id=course.id,
                category="exams",
                title="Midterm",
                description=None,
                file_key=f"users/{user.id}/courses/{course.id}/midterm.pdf",
                original_filename="midterm.pdf",
                mime_type="application/pdf",
                size_bytes=123,
            )
            session.add(content)
            await session.commit()
            await session.refresh(content)

        # Stub S3 client: bytes won't be parsed because we stub extraction.
        class _StubBody:
            def read(self):
                return b"%PDF-1.4 fake"

        class _StubS3:
            def get_object(self, *, Bucket, Key):
                assert Bucket == settings.s3_bucket
                assert Key == content.file_key
                return {"Body": _StubBody()}

        monkeypatch.setattr(pdf_ingestion, "_s3_client", lambda _settings: _StubS3())

        # Stub extraction to be deterministic and avoid PDF generation in tests.
        monkeypatch.setattr(
            pdf_ingestion,
            "_extract_pdf_pages",
            lambda _bytes: [
                pdf_ingestion.ExtractedPage(page_no=1, text="Question 1: Define eigenvalues."),
                pdf_ingestion.ExtractedPage(page_no=2, text="Question 2: Compute the determinant."),
            ],
        )

        await pdf_ingestion.ingest_pdf_content_to_db(content_id=content.id)

        async with SessionLocal() as session:
            refreshed = (await session.execute(select(CourseContent).where(CourseContent.id == content.id))).scalar_one()
            assert refreshed.ingestion_status in {"done", "warning"}
            pages = (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.content_id == content.id).order_by(DocumentPage.page_no.asc())
                )
            ).scalars().all()
            assert [p.page_no for p in pages] == [1, 2]
            assert "eigenvalues" in pages[0].text.lower()

            chunks = (
                await session.execute(
                    select(ContentChunk).where(ContentChunk.content_id == content.id).order_by(ContentChunk.chunk_index.asc())
                )
            ).scalars().all()
            assert len(chunks) >= 2
            assert all(c.course_id == course.id for c in chunks)
            assert all(c.category == "exams" for c in chunks)
            # Each chunk should carry page metadata.
            assert all(isinstance(c.meta, dict) and c.meta.get("page_start") for c in chunks)

        # Idempotency: second run should rebuild without violating unique constraints.
        await pdf_ingestion.ingest_pdf_content_to_db(content_id=content.id)
    finally:
        await engine.dispose()


