from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.services import video_summary as summary_svc


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
async def test_generate_and_store_video_asset_summary_writes_title_and_summary(monkeypatch) -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    # Patch LLM call so test is deterministic/offline.
    async def _mock_generate_reply(self, **kwargs):  # noqa: ANN001
        return (
            '{"title":"Server vs Serverless","summary":"## Key idea\\nServers cost money [#0:10]."}',
            [],
        )

    monkeypatch.setattr(summary_svc.ChatEngine, "generate_reply", _mock_generate_reply)

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
                title="Lecture 1",
                description=None,
                file_key="x",
                original_filename="x.mp4",
                mime_type="video/mp4",
            )
            session.add(content)
            await session.flush()

            asset = VideoAsset(course_id=course.id, content_id=content.id, source_file_key="x", mime_type="video/mp4")
            session.add(asset)
            await session.flush()

            session.add_all(
                [
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=0.0,
                        end_sec=1.0,
                        text="hello world",
                        language_code="en",
                    )
                ]
            )
            await session.commit()

            out = await summary_svc.generate_and_store_video_asset_summary(
                db=session, settings=settings, video_asset_id=asset.id, force=True
            )
            assert out is not None

            refreshed = (await session.execute(select(VideoAsset).where(VideoAsset.id == asset.id))).scalar_one()
            assert refreshed.ai_summary is not None
            assert "Servers cost money" in refreshed.ai_summary
            assert refreshed.ai_title is not None
            # enforce_title_constraints uses 3–5 words; ensure we got a usable short title.
            assert 3 <= len(refreshed.ai_title.split()) <= 5
            assert refreshed.ai_title_error is None
            assert refreshed.ai_summary_error is None
    finally:
        await engine.dispose()

