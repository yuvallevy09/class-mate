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
from app.db.models.video_asset import VideoAsset
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

            content_a = CourseContent(course_id=course.id, category="media", title="Lecture 1", description=None)
            content_b = CourseContent(course_id=course.id, category="media", title="Lecture 2", description=None)
            session.add_all([content_a, content_b])
            await session.commit()
            await session.refresh(content_a)
            await session.refresh(content_b)

            # Insert a couple chunks with overlapping vocabulary.
            session.add_all(
                [
                    ContentChunk(
                        course_id=course.id,
                        content_id=content_a.id,
                        category="media",
                        chunk_index=0,
                        text="Question 1: Define eigenvalues and eigenvectors.",
                        meta={"page_start": 1, "page_end": 1, "source_kind": "segment"},
                    ),
                    ContentChunk(
                        course_id=course.id,
                        content_id=content_b.id,
                        category="media",
                        chunk_index=0,
                        text="Eigenvalues appear in diagonalization.",
                        meta={"page_start": 5, "page_end": 5, "source_kind": "segment"},
                    ),
                ]
            )
            await session.commit()

            hits_all = await retrieve_course_chunk_hits(
                db=session, course_id=course.id, query="eigenvalues", top_k=10, categories=None
            )
            assert len(hits_all) >= 2

            hits_media = await retrieve_course_chunk_hits(
                db=session, course_id=course.id, query="eigenvalues", top_k=10, categories=["media"]
            )
            assert len(hits_media) >= 1
            assert all(h.metadata.get("category") == "media" for h in hits_media)

            # A non-matching category filter excludes everything.
            hits_other = await retrieve_course_chunk_hits(
                db=session, course_id=course.id, query="eigenvalues", top_k=10, categories=["notes"]
            )
            assert hits_other == []

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_retrieve_scopes_to_video_asset_ids_before_limit(monkeypatch) -> None:
    """fix B: `video_asset_ids` filters BEFORE the BM25 LIMIT, so a scoped
    lecture's chunk surfaces even when another lecture dominates the global rank.
    """
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

            # Two lectures (video assets). Lecture A has TWO matching chunks so it
            # would dominate a global ranking; lecture B has ONE.
            content_a = CourseContent(
                course_id=course.id, category="media", title="Lecture A", description=None,
                file_key="a", original_filename="a.mp4", mime_type="video/mp4",
            )
            content_b = CourseContent(
                course_id=course.id, category="media", title="Lecture B", description=None,
                file_key="b", original_filename="b.mp4", mime_type="video/mp4",
            )
            session.add_all([content_a, content_b])
            await session.flush()

            asset_a = VideoAsset(course_id=course.id, content_id=content_a.id, source_file_key="a", mime_type="video/mp4")
            asset_b = VideoAsset(course_id=course.id, content_id=content_b.id, source_file_key="b", mime_type="video/mp4")
            session.add_all([asset_a, asset_b])
            await session.commit()
            await session.refresh(asset_a)
            await session.refresh(asset_b)

            session.add_all(
                [
                    ContentChunk(
                        course_id=course.id, content_id=content_a.id, category="media",
                        chunk_index=0, text="Eigenvalues and eigenvectors, part one.",
                        meta={"source_kind": "segment"}, video_asset_id=asset_a.id,
                    ),
                    ContentChunk(
                        course_id=course.id, content_id=content_a.id, category="media",
                        chunk_index=1, text="Eigenvalues and eigenvectors, part two.",
                        meta={"source_kind": "segment"}, video_asset_id=asset_a.id,
                    ),
                    ContentChunk(
                        course_id=course.id, content_id=content_b.id, category="media",
                        chunk_index=0, text="Eigenvalues appear in diagonalization.",
                        meta={"source_kind": "segment"}, video_asset_id=asset_b.id,
                    ),
                ]
            )
            await session.commit()

            # Unscoped: sees both lectures.
            hits_all = await retrieve_course_chunk_hits(
                db=session, course_id=course.id, query="eigenvalues", top_k=10
            )
            assert {h.metadata.get("video_asset_id") for h in hits_all} == {str(asset_a.id), str(asset_b.id)}

            # Scoped to B with a tight top_k: B's chunk still surfaces (not crowded
            # out by A), and nothing from A leaks through.
            hits_b = await retrieve_course_chunk_hits(
                db=session, course_id=course.id, query="eigenvalues", top_k=2,
                video_asset_ids=[asset_b.id],
            )
            assert len(hits_b) == 1
            assert all(h.metadata.get("video_asset_id") == str(asset_b.id) for h in hits_b)

            # Scoped to an unrelated lecture id: empty.
            hits_none = await retrieve_course_chunk_hits(
                db=session, course_id=course.id, query="eigenvalues", top_k=10,
                video_asset_ids=[uuid4()],
            )
            assert hits_none == []

    finally:
        await engine.dispose()


