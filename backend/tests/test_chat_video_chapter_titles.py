from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.settings import get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.db.models.video_chapter import VideoChapter
from app.core.security import hash_password
from app.schemas.chat import ChatCitation
from app.services.chat_citations import attach_video_chapter_titles


def _run_migrations_sync() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


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


@pytest.mark.asyncio
async def test_attach_video_chapter_titles_resolves_from_chapter_id() -> None:
    """`attach_video_chapter_titles` should fill `chapterTitle` from the
    `video_chapters` table given a video citation carrying a `chapterId`.

    This is the helper the live v2 chat endpoint uses to enrich citations; the
    test exercises it directly against a real DB instead of through an endpoint.
    """
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

            chapter = VideoChapter(
                video_asset_id=asset.id,
                language_code="en",
                chapter_index=0,
                start_sec=0.0,
                end_sec=100.0,
                title="Full Lecture",
                description=None,
            )
            session.add(chapter)
            await session.commit()
            await session.refresh(chapter)

            citation = ChatCitation(
                content_id=content.id,
                title=None,
                url=None,
                snippet="snippet",
                extra={"type": "video", "startSec": 0, "endSec": 10, "chapterId": str(chapter.id)},
            )

            result = await attach_video_chapter_titles(
                db=session, course_id=course.id, citations=[citation]
            )

            assert result[0].extra is not None
            assert result[0].extra.get("chapterTitle") == "Full Lecture"
    finally:
        await engine.dispose()
