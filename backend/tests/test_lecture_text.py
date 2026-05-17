from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.rag.lecture_text import get_lecture_text
from app.services.course_info import build_course_info


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


async def _create_user_and_course(session) -> tuple[User, Course]:
    user = User(
        email=f"u-{uuid4()}@example.com",
        hashed_password=hash_password("pw"),
        display_name="T",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    course = Course(user_id=user.id, name="Networking", description=None)
    session.add(course)
    await session.commit()
    await session.refresh(course)
    return user, course


async def _create_lecture(
    session,
    *,
    course: Course,
    title: str,
    transcript_ready: bool = True,
) -> tuple[CourseContent, VideoAsset]:
    content = CourseContent(
        course_id=course.id,
        category="media",
        title=title,
        description=None,
        file_key=f"k-{uuid4()}",
        original_filename=f"{title}.mp4",
        mime_type="video/mp4",
    )
    session.add(content)
    await session.commit()
    await session.refresh(content)

    asset = VideoAsset(
        course_id=course.id,
        content_id=content.id,
        source_file_key=content.file_key or "",
        original_filename=content.original_filename,
        mime_type="video/mp4",
        transcript_ingested_at=datetime.now(timezone.utc) if transcript_ready else None,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return content, asset


async def _add_segments(session, *, course: Course, asset: VideoAsset, segments: list[tuple[float, float, str]]) -> None:
    session.add_all(
        [
            TranscriptSegment(
                course_id=course.id,
                video_asset_id=asset.id,
                start_sec=start,
                end_sec=end,
                text=body,
                language_code="en",
            )
            for (start, end, body) in segments
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_get_lecture_text_returns_short_lecture_under_budget() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            content, asset = await _create_lecture(session, course=course, title="Intro")
            await _add_segments(
                session,
                course=course,
                asset=asset,
                segments=[
                    (0.0, 10.0, "Welcome to the course."),
                    (10.0, 20.0, "Today we cover TCP."),
                    (20.0, 30.0, "Let's begin."),
                ],
            )

            info = await build_course_info(db=session, course=course)
            docs, is_long = await get_lecture_text(
                db=session,
                course_info=info,
                lecture_slugs=["L1"],
            )

            assert is_long is False
            assert len(docs) == 1
            doc = docs[0]
            assert doc.lecture_id == asset.id
            assert doc.lecture_slug == "L1"
            assert doc.lecture_title == "Intro"
            assert doc.content_id == content.id
            assert doc.chapter_id is None
            assert doc.start_sec == 0.0
            assert doc.end_sec == 30.0
            assert "[0:00] Welcome to the course." in doc.text
            assert "[0:10] Today we cover TCP." in doc.text
            assert "[0:20] Let's begin." in doc.text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_lecture_text_flags_long_lecture_against_char_budget() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            _, asset = await _create_lecture(session, course=course, title="Long Lecture")

            big = "x" * 500
            segments = [(float(i * 10), float((i + 1) * 10), big) for i in range(20)]
            await _add_segments(session, course=course, asset=asset, segments=segments)

            info = await build_course_info(db=session, course=course)
            docs, is_long = await get_lecture_text(
                db=session,
                course_info=info,
                lecture_slugs=["L1"],
                char_budget=2000,
            )

            assert is_long is True
            assert len(docs) == 1  # we still return the doc; caller decides to drop it
            assert len(docs[0].text) > 2000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_lecture_text_combined_budget_across_multiple_lectures() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            _, asset_a = await _create_lecture(session, course=course, title="A")
            await asyncio.sleep(0.05)
            _, asset_b = await _create_lecture(session, course=course, title="B")

            await _add_segments(
                session,
                course=course,
                asset=asset_a,
                segments=[(0.0, 10.0, "a" * 600)],
            )
            await _add_segments(
                session,
                course=course,
                asset=asset_b,
                segments=[(0.0, 10.0, "b" * 600)],
            )

            info = await build_course_info(db=session, course=course)

            # Each lecture alone fits; combined does not.
            docs, is_long = await get_lecture_text(
                db=session,
                course_info=info,
                lecture_slugs=["L1", "L2"],
                char_budget=1000,
            )
            assert is_long is True
            assert len(docs) == 2
            assert [d.lecture_slug for d in docs] == ["L1", "L2"]

            # With a generous budget, both fit.
            docs, is_long = await get_lecture_text(
                db=session,
                course_info=info,
                lecture_slugs=["L1", "L2"],
                char_budget=10_000,
            )
            assert is_long is False
            assert len(docs) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_lecture_text_skips_lectures_with_transcript_not_ready() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            _, asset_ready = await _create_lecture(session, course=course, title="Ready")
            await asyncio.sleep(0.05)
            _, asset_pending = await _create_lecture(
                session, course=course, title="Pending", transcript_ready=False
            )

            await _add_segments(
                session,
                course=course,
                asset=asset_ready,
                segments=[(0.0, 5.0, "ready content")],
            )
            await _add_segments(
                session,
                course=course,
                asset=asset_pending,
                segments=[(0.0, 5.0, "pending content (ignored)")],
            )

            info = await build_course_info(db=session, course=course)
            docs, is_long = await get_lecture_text(
                db=session,
                course_info=info,
                lecture_slugs=["L1", "L2"],
            )

            assert is_long is False
            assert len(docs) == 1
            assert docs[0].lecture_slug == "L1"
            assert "pending content" not in docs[0].text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_lecture_text_returns_empty_for_no_or_unknown_slugs() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            _, asset = await _create_lecture(session, course=course, title="Only")
            await _add_segments(
                session,
                course=course,
                asset=asset,
                segments=[(0.0, 5.0, "hello")],
            )

            info = await build_course_info(db=session, course=course)

            assert await get_lecture_text(db=session, course_info=info, lecture_slugs=None) == ([], False)
            assert await get_lecture_text(db=session, course_info=info, lecture_slugs=[]) == ([], False)
            assert await get_lecture_text(db=session, course_info=info, lecture_slugs=["L99"]) == ([], False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_lecture_text_skips_lectures_with_no_segments() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            # "Ready" but no segments — e.g. ingestion edge case.
            await _create_lecture(session, course=course, title="Empty Ready")

            info = await build_course_info(db=session, course=course)
            docs, is_long = await get_lecture_text(
                db=session,
                course_info=info,
                lecture_slugs=["L1"],
            )
            assert docs == []
            assert is_long is False
    finally:
        await engine.dispose()
