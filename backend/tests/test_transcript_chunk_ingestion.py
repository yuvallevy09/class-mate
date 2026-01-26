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
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.services.transcript_chunk_ingestion import ingest_video_asset_transcript_to_chunks


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
async def test_transcript_segments_are_ingested_into_content_chunks(monkeypatch) -> None:
    # Ensure no external embedding calls during test.
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

            content = CourseContent(course_id=course.id, category="media", title="Lecture 1", description=None)
            session.add(content)
            await session.commit()
            await session.refresh(content)

            asset = VideoAsset(
                course_id=course.id,
                content_id=content.id,
                source_file_key="x",
                original_filename="lecture1.mp4",
                mime_type="video/mp4",
                size_bytes=123,
            )
            session.add(asset)
            await session.commit()
            await session.refresh(asset)

            session.add_all(
                [
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=0.0,
                        end_sec=5.0,
                        text="Today we introduce eigenvalues.",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=5.0,
                        end_sec=10.0,
                        text="We will also cover eigenvectors.",
                        language_code="en",
                    ),
                ]
            )
            await session.commit()

            n = await ingest_video_asset_transcript_to_chunks(db=session, video_asset_id=asset.id, language_code="en")
            await session.commit()
            assert n >= 1

            res = await session.execute(
                select(ContentChunk).where(ContentChunk.content_id == content.id).order_by(ContentChunk.chunk_index.asc())
            )
            chunks = list(res.scalars().all())
            assert len(chunks) == n
            assert chunks[0].meta.get("doc_type") == "segment"
            assert chunks[0].meta.get("video_asset_id") == str(asset.id)
            assert chunks[0].meta.get("language_code") == "en"
    finally:
        await engine.dispose()


