"""Integration test for `generate_and_store_lecture_artifacts`.

Uses a real Postgres (skips when unreachable, mirroring
`test_video_asset_summary_title.py`) but injects a fake generator so no LLM /
network call happens. Verifies the three artifact columns are persisted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai.lecture_artifact_generator import LectureArtifacts
from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.services import lecture_artifacts as svc


class _FakeGenerator:
    """Stand-in for `LectureArtifactGenerator` with a canned async result."""

    def __init__(self, artifacts: LectureArtifacts) -> None:
        self._artifacts = artifacts
        self.calls: list[str] = []

    async def aforward(self, *, lecture_transcript: str) -> LectureArtifacts:
        self.calls.append(lecture_transcript)
        return self._artifacts


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
async def test_generates_and_stores_title_desc_summary() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    fake = _FakeGenerator(
        LectureArtifacts(
            generated_title="Server vs Serverless",
            generated_desc="Compares servers and serverless architectures.",
            generated_summary="## Key idea\nServers cost money [#0:10].",
        )
    )

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

            session.add(
                TranscriptSegment(
                    course_id=course.id,
                    video_asset_id=asset.id,
                    start_sec=0.0,
                    end_sec=1.0,
                    text="hello world",
                    language_code="en",
                )
            )
            await session.commit()

            out = await svc.generate_and_store_lecture_artifacts(
                db=session,
                settings=settings,
                video_asset_id=asset.id,
                force=True,
                generator=fake,
            )
            assert out is not None
            assert fake.calls and "Transcript (timestamped):" in fake.calls[0]

            refreshed = (
                await session.execute(select(VideoAsset).where(VideoAsset.id == asset.id))
            ).scalar_one()
            assert refreshed.ai_title == "Server vs Serverless"
            assert refreshed.ai_description == "Compares servers and serverless architectures."
            assert refreshed.ai_summary is not None
            assert "[#0:10]" in refreshed.ai_summary
            assert refreshed.ai_title_error is None
            assert refreshed.ai_description_error is None
            assert refreshed.ai_summary_error is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_marks_error_when_transcript_missing() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    fake = _FakeGenerator(
        LectureArtifacts(generated_title="x", generated_desc="y", generated_summary="z")
    )

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
                file_key="x",
                mime_type="video/mp4",
            )
            session.add(content)
            await session.flush()

            asset = VideoAsset(course_id=course.id, content_id=content.id, source_file_key="x", mime_type="video/mp4")
            session.add(asset)
            await session.commit()

            out = await svc.generate_and_store_lecture_artifacts(
                db=session,
                settings=settings,
                video_asset_id=asset.id,
                force=True,
                generator=fake,
            )
            assert out is not None
            assert not fake.calls  # generator never invoked without a transcript

            refreshed = (
                await session.execute(select(VideoAsset).where(VideoAsset.id == asset.id))
            ).scalar_one()
            assert refreshed.ai_title is None
            assert refreshed.ai_title_error == "Transcript not available yet"
            assert refreshed.ai_description_error == "Transcript not available yet"
            assert refreshed.ai_summary_error == "Transcript not available yet"
    finally:
        await engine.dispose()
