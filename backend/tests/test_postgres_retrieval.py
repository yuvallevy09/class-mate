from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.content_chunk import ContentChunk
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.user import User
from app.rag.pg_retrieve import retrieve_course_chunk_hits


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
async def test_postgres_retrieve_filters_by_category(monkeypatch) -> None:
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

            content_exam = CourseContent(course_id=course.id, category="exams", title="Midterm", description=None)
            content_notes = CourseContent(course_id=course.id, category="notes", title="Lecture", description=None)
            session.add_all([content_exam, content_notes])
            await session.commit()
            await session.refresh(content_exam)
            await session.refresh(content_notes)

            # Insert a couple chunks with overlapping vocabulary.
            session.add_all(
                [
                    ContentChunk(
                        course_id=course.id,
                        content_id=content_exam.id,
                        category="exams",
                        chunk_index=0,
                        text="Question 1: Define eigenvalues and eigenvectors.",
                        meta={"page_start": 1, "page_end": 1, "source_kind": "pdf"},
                    ),
                    ContentChunk(
                        course_id=course.id,
                        content_id=content_notes.id,
                        category="notes",
                        chunk_index=0,
                        text="Eigenvalues appear in diagonalization.",
                        meta={"page_start": 5, "page_end": 5, "source_kind": "pdf"},
                    ),
                ]
            )
            await session.commit()

            hits_all = await retrieve_course_chunk_hits(
                db=session, course_id=course.id, query="eigenvalues", top_k=10, categories=None
            )
            assert len(hits_all) >= 2

            hits_exams = await retrieve_course_chunk_hits(
                db=session, course_id=course.id, query="eigenvalues", top_k=10, categories=["exams"]
            )
            assert len(hits_exams) >= 1
            assert all(h.metadata.get("category") == "exams" for h in hits_exams)

    finally:
        await engine.dispose()


