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
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.db.models.video_chapter import VideoChapter
from app.services import video_chapters as chapters_svc


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
async def test_chapterize_or_fallback_uses_semantic_when_available(monkeypatch) -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    # Patch DSPy call to avoid network.
    def _fake_generate_chapters_dspy(*, settings, blocks):  # noqa: ANN001
        # Two chapters split by block index.
        return {
            "chapters": [
                {"start_block": 0, "end_block": 0, "title": "Intro", "description": "Start"},
                {"start_block": 1, "end_block": 2, "title": "Main Topic", "description": "Core"},
            ]
        }

    monkeypatch.setattr(chapters_svc, "generate_chapters_dspy", _fake_generate_chapters_dspy)

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
                title="Video",
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

            # Create segments covering ~60s, enough for multiple blocks.
            session.add_all(
                [
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=0.0,
                        end_sec=10.0,
                        text="Intro part",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=10.0,
                        end_sec=20.0,
                        text="Intro continues",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=20.0,
                        end_sec=30.0,
                        text="Main topic part 1",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=30.0,
                        end_sec=40.0,
                        text="Main topic part 2",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=40.0,
                        end_sec=50.0,
                        text="Wrap-up part 1",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=50.0,
                        end_sec=60.0,
                        text="Wrap-up part 2",
                        language_code="en",
                    ),
                ]
            )
            await session.commit()

            rows = await chapters_svc.chapterize_or_fallback(
                db=session, video_asset_id=asset.id, language_code="en", end_sec=60.0
            )
            await session.commit()
            assert len(rows) == 2

            res = await session.execute(
                select(VideoChapter).where(VideoChapter.video_asset_id == asset.id).order_by(VideoChapter.chapter_index.asc())
            )
            persisted = list(res.scalars().all())
            assert [c.title for c in persisted] == ["Intro", "Main Topic"]
    finally:
        await engine.dispose()

