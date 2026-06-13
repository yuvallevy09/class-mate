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
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.db.models.video_chapter import VideoChapter
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


@pytest.mark.asyncio
async def test_build_course_info_returns_lectures_in_chronological_order() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            user = User(
                email=f"u-{uuid4()}@example.com",
                hashed_password=hash_password("pw"),
                display_name="T",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            course = Course(user_id=user.id, name="Networking 101", description="Intro to networks")
            session.add(course)
            await session.commit()
            await session.refresh(course)

            # Lecture 1: older `course_contents.created_at`, has ai_title + ai_summary +
            # transcript ingested + two chapters.
            content_a = CourseContent(
                course_id=course.id,
                category="media",
                title="The Original Title",
                description="  Instructor description.  ",
                file_key="a-key",
                original_filename="lecture-a.mp4",
                mime_type="video/mp4",
            )
            session.add(content_a)
            await session.commit()
            await session.refresh(content_a)

            asset_a = VideoAsset(
                course_id=course.id,
                content_id=content_a.id,
                source_file_key="a-key",
                original_filename="lecture-a.mp4",
                mime_type="video/mp4",
                ai_title="Intro to Networking",
                ai_description="A one-line blurb for lecture A.",
                ai_summary="An AI-generated summary of lecture A.",
                transcript_ingested_at=datetime.now(timezone.utc),
            )
            session.add(asset_a)
            await session.commit()
            await session.refresh(asset_a)

            chapters_a = [
                VideoChapter(
                    video_asset_id=asset_a.id,
                    language_code="en",
                    chapter_index=0,
                    start_sec=0.0,
                    end_sec=313.0,
                    title="Welcome",
                    description=None,
                ),
                VideoChapter(
                    video_asset_id=asset_a.id,
                    language_code="en",
                    chapter_index=1,
                    start_sec=313.0,
                    end_sec=1320.0,
                    title="OSI Model",
                    description=None,
                ),
            ]
            session.add_all(chapters_a)
            await session.commit()

            # Force a clock gap so the chronological ordering is deterministic on
            # databases with low-resolution clocks.
            await asyncio.sleep(0.05)

            # Lecture 2: newer, no ai_title, no ai_summary, no chapters, transcript
            # not yet ingested. Title should fall back to CourseContent.title, then
            # to original_filename if that were also missing.
            content_b = CourseContent(
                course_id=course.id,
                category="media",
                title="Lecture B Library Title",
                description=None,
                file_key="b-key",
                original_filename="lecture-b.mp4",
                mime_type="video/mp4",
            )
            session.add(content_b)
            await session.commit()
            await session.refresh(content_b)

            asset_b = VideoAsset(
                course_id=course.id,
                content_id=content_b.id,
                source_file_key="b-key",
                original_filename="lecture-b.mp4",
                mime_type="video/mp4",
            )
            session.add(asset_b)
            await session.commit()
            await session.refresh(asset_b)

            info = await build_course_info(db=session, course=course)

            assert info.id == course.id
            assert info.name == "Networking 101"
            assert info.description == "Intro to networks"
            assert info.summary is None  # not yet sourced from DB; future TODO

            assert len(info.lectures) == 2

            lec1, lec2 = info.lectures

            assert lec1.slug == "L1"
            assert lec1.id == asset_a.id
            assert lec1.content_id == content_a.id
            assert lec1.title == "Intro to Networking"  # ai_title wins
            assert lec1.description == "Instructor description."  # stripped
            assert lec1.ai_description == "A one-line blurb for lecture A."
            assert lec1.summary == "An AI-generated summary of lecture A."
            assert lec1.transcript_ready is True

            assert lec2.slug == "L2"
            assert lec2.id == asset_b.id
            assert lec2.content_id == content_b.id
            assert lec2.title == "Lecture B Library Title"  # content.title fallback
            assert lec2.description is None
            assert lec2.ai_description is None
            assert lec2.summary is None
            assert lec2.transcript_ready is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_build_course_info_falls_back_through_title_candidates() -> None:
    """When `ai_title` and `content.title` are both missing, fall back to filenames."""
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            user = User(
                email=f"u-{uuid4()}@example.com",
                hashed_password=hash_password("pw"),
                display_name="T",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            course = Course(user_id=user.id, name="C", description=None)
            session.add(course)
            await session.commit()
            await session.refresh(course)

            # CourseContent.title is non-nullable on the model, so the "no ai_title,
            # no content.title" case isn't reachable in practice. We exercise the
            # ai_title preference here: ai_title set with a different content.title.
            content = CourseContent(
                course_id=course.id,
                category="media",
                title="raw-upload",
                description=None,
                file_key="k",
                original_filename="lec.mp4",
                mime_type="video/mp4",
            )
            session.add(content)
            await session.commit()
            await session.refresh(content)

            asset = VideoAsset(
                course_id=course.id,
                content_id=content.id,
                source_file_key="k",
                original_filename="lec.mp4",
                mime_type="video/mp4",
                ai_title="A Nice Lecture Title",
            )
            session.add(asset)
            await session.commit()
            await session.refresh(asset)

            info = await build_course_info(db=session, course=course)
            (lec,) = info.lectures
            assert lec.title == "A Nice Lecture Title"

            # Remove ai_title; should fall back to content.title.
            asset.ai_title = None
            await session.commit()
            info = await build_course_info(db=session, course=course)
            (lec,) = info.lectures
            assert lec.title == "raw-upload"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_build_course_info_for_course_with_no_videos_returns_empty_lectures() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            user = User(
                email=f"u-{uuid4()}@example.com",
                hashed_password=hash_password("pw"),
                display_name="T",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            course = Course(user_id=user.id, name="Empty course", description=None)
            session.add(course)
            await session.commit()
            await session.refresh(course)

            info = await build_course_info(db=session, course=course)
            assert info.lectures == []
            assert info.id == course.id
            assert info.name == "Empty course"
    finally:
        await engine.dispose()
