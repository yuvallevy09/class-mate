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
from app.db.models.video_chapter import VideoChapter
from app.rag.hybrid_retrieve import HybridRetrieveConfig, retrieve_course_hybrid_hits


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
async def test_hybrid_retrieval_falls_back_to_lexical_when_no_embeddings(monkeypatch) -> None:
    # Ensure embeddings provider isn't configured so semantic retrieval returns empty.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RAG_EMBEDDINGS_PROVIDER", "openai")
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

            content = CourseContent(course_id=course.id, category="notes", title="Lecture", description=None)
            session.add(content)
            await session.commit()
            await session.refresh(content)

            # Two chunks that should match lexically.
            session.add_all(
                [
                    ContentChunk(
                        course_id=course.id,
                        content_id=content.id,
                        category="notes",
                        chunk_index=0,
                        text="Matrix multiplication is associative.",
                        meta={"doc_type": "pdf", "page_start": 1, "page_end": 1},
                    ),
                    ContentChunk(
                        course_id=course.id,
                        content_id=content.id,
                        category="notes",
                        chunk_index=1,
                        text="A stack is a LIFO data structure.",
                        meta={"doc_type": "pdf", "page_start": 2, "page_end": 2},
                    ),
                ]
            )
            await session.commit()

            hits = await retrieve_course_hybrid_hits(
                db=session,
                course_id=course.id,
                query="matrix multiplication",
                cfg=HybridRetrieveConfig(lexical_k=5, semantic_k=5, top_k=3, rrf_k0=60),
            )
            assert len(hits) >= 1
            # With no embeddings configured, we should return lexical hits with no RRF sources metadata.
            assert "rrf" not in (hits[0].metadata or {})
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_hybrid_retrieval_neighbor_expansion_adds_adjacent_chunks(monkeypatch) -> None:
    # Disable embeddings to force lexical-only retrieval.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RAG_EMBEDDINGS_PROVIDER", "openai")
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
                category="media",
                title="Lecture Video",
                description=None,
                file_key="x",
                original_filename="x.mp4",
                mime_type="video/mp4",
            )
            session.add(content)
            await session.flush()

            asset = VideoAsset(course_id=course.id, content_id=content.id, source_file_key="x", mime_type="video/mp4")
            session.add(asset)
            await session.commit()
            await session.refresh(asset)

            chapter = VideoChapter(
                video_asset_id=asset.id,
                language_code="en",
                chapter_index=0,
                start_sec=0.0,
                end_sec=90.0,
                title="Full Lecture",
                description=None,
            )
            session.add(chapter)
            await session.commit()
            await session.refresh(chapter)

            # Three adjacent chunks in the same chapter. The middle one should be the lexical match.
            session.add_all(
                [
                    ContentChunk(
                        course_id=course.id,
                        content_id=content.id,
                        category="media",
                        chunk_index=0,
                        text="Intro: servers vs serverless overview.",
                        meta={"source_kind": "video", "language_code": "en"},
                        video_asset_id=asset.id,
                        chapter_id=chapter.id,
                        chunk_index_in_chapter=0,
                        chunk_start_sec=0.0,
                        chunk_end_sec=30.0,
                    ),
                    ContentChunk(
                        course_id=course.id,
                        content_id=content.id,
                        category="media",
                        chunk_index=1,
                        text="Servers are always running and you pay continuously.",
                        meta={"source_kind": "video", "language_code": "en"},
                        video_asset_id=asset.id,
                        chapter_id=chapter.id,
                        chunk_index_in_chapter=1,
                        chunk_start_sec=30.0,
                        chunk_end_sec=60.0,
                    ),
                    ContentChunk(
                        course_id=course.id,
                        content_id=content.id,
                        category="media",
                        chunk_index=2,
                        text="Serverless is pay-per-request with provider-managed scaling.",
                        meta={"source_kind": "video", "language_code": "en"},
                        video_asset_id=asset.id,
                        chapter_id=chapter.id,
                        chunk_index_in_chapter=2,
                        chunk_start_sec=60.0,
                        chunk_end_sec=90.0,
                    ),
                ]
            )
            await session.commit()

            hits = await retrieve_course_hybrid_hits(
                db=session,
                course_id=course.id,
                query="pay continuously",
                cfg=HybridRetrieveConfig(
                    lexical_k=5,
                    semantic_k=0,
                    top_k=1,
                    rrf_k0=60,
                    neighbor_before=1,
                    neighbor_after=1,
                    neighbor_max_additional=2,
                ),
                categories=["media"],
            )

            assert hits
            # We should include at least one adjacent neighbor chunk.
            assert any((h.metadata or {}).get("neighbor") is True for h in hits)

            idxs = [
                int((h.metadata or {}).get("chunk_index_in_chapter"))
                for h in hits
                if (h.metadata or {}).get("chapter_id") == str(chapter.id)
                and (h.metadata or {}).get("chunk_index_in_chapter") is not None
            ]
            # Neighbor expansion should yield a contiguous window of indices.
            assert len(set(idxs)) >= 2
            sidxs = sorted(set(idxs))
            assert all((b - a) == 1 for a, b in zip(sidxs, sidxs[1:]))
    finally:
        await engine.dispose()


