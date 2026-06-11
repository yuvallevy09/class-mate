"""Integration tests for `generate_and_store_course_summary`.

Uses a real Postgres (skips when unreachable, mirroring
`test_lecture_artifacts_service.py`) but injects a fake generator so no LLM /
network call happens. Verifies replace-on-success semantics: the old summary
survives failures and is only overwritten once a new one is fully generated.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai.course_summary_generator import CourseSummary
from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.services import course_summary as svc


class _FakeGenerator:
    """Stand-in for `CourseSummaryGenerator` with a canned async result."""

    def __init__(self, summary: CourseSummary | None = None, *, error: Exception | None = None) -> None:
        self._summary = summary
        self._error = error
        self.calls: list[str] = []

    async def aforward(self, *, course_context: str) -> CourseSummary:
        self.calls.append(course_context)
        if self._error is not None:
            raise self._error
        assert self._summary is not None
        return self._summary


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


async def _seed_course_with_lectures(session, *, lecture_summaries: list[str | None]) -> Course:
    """Create a user + course + one done video asset per entry in `lecture_summaries`."""
    user = User(email=f"u-{uuid4()}@e.com", hashed_password=hash_password("pw"), display_name="T")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    course = Course(user_id=user.id, name="Linear Algebra", description="Vectors and matrices.")
    session.add(course)
    await session.commit()
    await session.refresh(course)

    for i, summary in enumerate(lecture_summaries, start=1):
        content = CourseContent(
            course_id=course.id,
            category="media",
            title=f"Lecture {i} title",
            description=None,
            file_key=f"k{i}",
            original_filename=f"l{i}.mp4",
            mime_type="video/mp4",
        )
        session.add(content)
        await session.flush()

        asset = VideoAsset(
            course_id=course.id,
            content_id=content.id,
            source_file_key=f"k{i}",
            mime_type="video/mp4",
            status="done",
            ai_summary=summary,
        )
        session.add(asset)
    await session.commit()
    return course


async def _get_course(session, course_id) -> Course:
    return (await session.execute(select(Course).where(Course.id == course_id))).scalar_one()


@pytest.mark.asyncio
async def test_generates_and_stores_summary_from_lecture_summaries() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")
    await asyncio.to_thread(_run_migrations_sync)

    fake = _FakeGenerator(CourseSummary(generated_summary="The course built up linear algebra."))

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            course = await _seed_course_with_lectures(
                session, lecture_summaries=["Covers vectors.", "Covers matrices."]
            )

            out = await svc.generate_and_store_course_summary(
                db=session, settings=settings, course_id=course.id, generator=fake
            )
            assert out is not None

            # Context contains both lectures, chronological order.
            assert len(fake.calls) == 1
            ctx = fake.calls[0]
            assert "Course: Linear Algebra" in ctx
            assert ctx.index("Covers vectors.") < ctx.index("Covers matrices.")

            refreshed = await _get_course(session, course.id)
            assert refreshed.ai_summary == "The course built up linear algebra."
            assert refreshed.ai_summary_generated_at is not None
            assert refreshed.ai_summary_error is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_replaces_existing_summary_on_success() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")
    await asyncio.to_thread(_run_migrations_sync)

    fake = _FakeGenerator(CourseSummary(generated_summary="New summary."))

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            course = await _seed_course_with_lectures(session, lecture_summaries=["Covers vectors."])
            course.ai_summary = "Old summary."
            course.ai_summary_generated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            course.ai_summary_error = "previous failure"
            await session.commit()

            await svc.generate_and_store_course_summary(
                db=session, settings=settings, course_id=course.id, generator=fake
            )

            refreshed = await _get_course(session, course.id)
            assert refreshed.ai_summary == "New summary."
            assert refreshed.ai_summary_generated_at > datetime(2026, 1, 1, tzinfo=timezone.utc)
            assert refreshed.ai_summary_error is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_keeps_old_summary_when_generation_fails() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")
    await asyncio.to_thread(_run_migrations_sync)

    fake = _FakeGenerator(error=RuntimeError("LM exploded"))

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            course = await _seed_course_with_lectures(session, lecture_summaries=["Covers vectors."])
            course.ai_summary = "Old summary."
            await session.commit()

            out = await svc.generate_and_store_course_summary(
                db=session, settings=settings, course_id=course.id, generator=fake
            )
            assert out is not None  # never raises

            refreshed = await _get_course(session, course.id)
            assert refreshed.ai_summary == "Old summary."
            assert refreshed.ai_summary_error == "LM exploded"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_noop_when_no_usable_lecture_summaries() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")
    await asyncio.to_thread(_run_migrations_sync)

    fake = _FakeGenerator(CourseSummary(generated_summary="Should never be used."))

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            # One done lecture, but with neither ai_summary nor ai_description.
            course = await _seed_course_with_lectures(session, lecture_summaries=[None])

            out = await svc.generate_and_store_course_summary(
                db=session, settings=settings, course_id=course.id, generator=fake
            )
            assert out is not None
            assert not fake.calls  # generator never invoked

            refreshed = await _get_course(session, course.id)
            assert refreshed.ai_summary is None
            assert refreshed.ai_summary_error is None
    finally:
        await engine.dispose()
