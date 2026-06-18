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
from app.db.models.video_chapter import VideoChapter
from app.rag.explicit_retrieve import retrieve_explicitly, retrieve_recent_window
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


@pytest.mark.asyncio
async def test_retrieve_explicitly_returns_chapter_bounded_transcript_when_in_chapter() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            content, asset = await _create_lecture(session, course=course, title="Lecture A")

            session.add_all(
                [
                    VideoChapter(
                        video_asset_id=asset.id,
                        language_code="en",
                        chapter_index=0,
                        start_sec=0.0,
                        end_sec=600.0,
                        title="Welcome",
                        description=None,
                        model_id="test-chapter-model",
                    ),
                    VideoChapter(
                        video_asset_id=asset.id,
                        language_code="en",
                        chapter_index=1,
                        start_sec=600.0,
                        end_sec=1200.0,
                        title="OSI Model",
                        description=None,
                        model_id="test-chapter-model",
                    ),
                    VideoChapter(
                        video_asset_id=asset.id,
                        language_code="en",
                        chapter_index=2,
                        start_sec=1200.0,
                        end_sec=2400.0,
                        title="TCP Handshake",
                        description=None,
                        model_id="test-chapter-model",
                    ),
                ]
            )

            session.add_all(
                [
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=0.0,
                        end_sec=60.0,
                        text="Welcome to the course.",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=1500.0,
                        end_sec=1530.0,
                        text="The TCP handshake has three steps.",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=1530.0,
                        end_sec=1560.0,
                        text="First the client sends a SYN.",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=2500.0,
                        end_sec=2530.0,
                        text="That concludes the lecture.",
                        language_code="en",
                    ),
                ]
            )
            await session.commit()

            info = await build_course_info(db=session, course=course)
            assert info.lectures[0].slug == "L1"

            # 25:30 falls inside the TCP Handshake chapter (1200–2400).
            docs = await retrieve_explicitly(
                db=session,
                course_info=info,
                lecture_slug="L1",
                timestamp="25:30",
            )
            assert len(docs) == 1
            doc = docs[0]
            assert doc.lecture_id == asset.id
            assert doc.lecture_slug == "L1"
            assert doc.lecture_title == "Lecture A"
            assert doc.content_id == content.id
            assert doc.chapter_title == "TCP Handshake"
            assert doc.start_sec == 1200.0
            assert doc.end_sec == 2400.0
            assert "TCP handshake" in doc.text
            assert "First the client sends a SYN." in doc.text
            assert "Welcome to the course." not in doc.text
            assert "That concludes the lecture." not in doc.text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_explicitly_ignores_fallback_chapter_and_uses_bounded_window() -> None:
    # Regression: a "Full Lecture" fallback chapter (model_id IS NULL) spans the
    # whole video. It must NOT be used as the retrieval window — otherwise a
    # timestamped/deictic question in video mode cites the entire lecture
    # (the "0:00–62:05 pill" bug). Explicit retrieval should ignore it and fall
    # back to the bounded window around the timestamp.
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            content, asset = await _create_lecture(session, course=course, title="Lecture A")

            # Single fallback chapter spanning the whole lecture (model_id=None).
            session.add(
                VideoChapter(
                    video_asset_id=asset.id,
                    language_code="en",
                    chapter_index=0,
                    start_sec=0.0,
                    end_sec=3600.0,
                    title="Full Lecture",
                    description=None,
                    model_id=None,
                )
            )
            session.add_all(
                [
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=0.0,
                        end_sec=30.0,
                        text="Way back at the start.",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=1500.0,
                        end_sec=1530.0,
                        text="The moment in question.",
                        language_code="en",
                    ),
                ]
            )
            await session.commit()

            info = await build_course_info(db=session, course=course)
            docs = await retrieve_explicitly(
                db=session,
                course_info=info,
                lecture_slug="L1",
                timestamp="25:00",  # 1500s
                fallback_window_sec=60.0,
            )
            assert len(docs) == 1
            doc = docs[0]
            # Fallback chapter ignored: no chapter attribution, bounded window only.
            assert doc.chapter_title is None
            assert doc.start_sec != 0.0
            assert doc.end_sec < 3600.0
            assert "The moment in question." in doc.text
            assert "Way back at the start." not in doc.text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_explicitly_falls_back_to_window_when_no_chapter_contains_timestamp() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            _, asset = await _create_lecture(session, course=course, title="Lecture B")

            session.add_all(
                [
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=0.0,
                        end_sec=10.0,
                        text="zero zero",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=290.0,
                        end_sec=310.0,
                        text="around five minutes",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=600.0,
                        end_sec=610.0,
                        text="ten minute mark",
                        language_code="en",
                    ),
                ]
            )
            await session.commit()

            info = await build_course_info(db=session, course=course)

            docs = await retrieve_explicitly(
                db=session,
                course_info=info,
                lecture_slug="L1",
                timestamp="5:00",
                fallback_window_sec=60.0,
            )
            assert len(docs) == 1
            doc = docs[0]
            assert doc.chapter_id is None
            assert doc.chapter_title is None
            assert "around five minutes" in doc.text
            assert "zero zero" not in doc.text
            assert "ten minute mark" not in doc.text
            assert doc.start_sec == pytest.approx(290.0)
            assert doc.end_sec == pytest.approx(310.0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_explicitly_returns_empty_for_unknown_or_malformed_spec() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            _, asset = await _create_lecture(session, course=course, title="Only Lecture")

            session.add(
                TranscriptSegment(
                    course_id=course.id,
                    video_asset_id=asset.id,
                    start_sec=0.0,
                    end_sec=10.0,
                    text="hello",
                    language_code="en",
                )
            )
            await session.commit()

            info = await build_course_info(db=session, course=course)

            async def call(slug: str | None, ts: str | None) -> list:
                return await retrieve_explicitly(
                    db=session,
                    course_info=info,
                    lecture_slug=slug,
                    timestamp=ts,
                )

            # Both None / empty -> empty result.
            assert await call(None, None) == []
            assert await call("", "") == []
            # Missing timestamp -> empty (don't guess).
            assert await call("L1", None) == []
            assert await call("L1", "") == []
            # Missing slug -> empty (don't guess).
            assert await call(None, "27:36") == []
            # Malformed slug -> empty.
            assert await call("garbage", "1:00") == []
            # Slug doesn't resolve to a real lecture -> empty.
            assert await call("L99", "1:00") == []
            # Malformed timestamp -> empty.
            assert await call("L1", "abc") == []
            assert await call("L1", "27:99") == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_explicitly_returns_empty_when_transcript_not_ready() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            _, asset = await _create_lecture(
                session,
                course=course,
                title="Pending Lecture",
                transcript_ready=False,
            )

            # Even with segments present, lecture marked not-ready should bail out.
            session.add(
                TranscriptSegment(
                    course_id=course.id,
                    video_asset_id=asset.id,
                    start_sec=0.0,
                    end_sec=10.0,
                    text="hello",
                    language_code="en",
                )
            )
            await session.commit()

            info = await build_course_info(db=session, course=course)
            assert info.lectures[0].transcript_ready is False

            docs = await retrieve_explicitly(
                db=session,
                course_info=info,
                lecture_slug="L1",
                timestamp="0:05",
            )
            assert docs == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_explicitly_returns_empty_when_no_overlapping_segments() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            _, asset = await _create_lecture(session, course=course, title="Sparse Lecture")

            session.add(
                TranscriptSegment(
                    course_id=course.id,
                    video_asset_id=asset.id,
                    start_sec=0.0,
                    end_sec=5.0,
                    text="only at the start",
                    language_code="en",
                )
            )
            await session.commit()

            info = await build_course_info(db=session, course=course)

            docs = await retrieve_explicitly(
                db=session,
                course_info=info,
                lecture_slug="L1",
                timestamp="30:00",
                fallback_window_sec=10.0,
            )
            assert docs == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_explicitly_source_label_and_prompt_string() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            _, asset = await _create_lecture(session, course=course, title="Intro")

            session.add(
                VideoChapter(
                    video_asset_id=asset.id,
                    language_code="en",
                    chapter_index=0,
                    start_sec=0.0,
                    end_sec=600.0,
                    title="Welcome",
                    description=None,
                    model_id="test-chapter-model",
                )
            )
            session.add(
                TranscriptSegment(
                    course_id=course.id,
                    video_asset_id=asset.id,
                    start_sec=10.0,
                    end_sec=20.0,
                    text="hello students",
                    language_code="en",
                )
            )
            await session.commit()

            info = await build_course_info(db=session, course=course)
            docs = await retrieve_explicitly(
                db=session,
                course_info=info,
                lecture_slug="L1",
                timestamp="0:15",
            )
            assert len(docs) == 1
            doc = docs[0]
            label = doc.source_label
            assert "Intro" in label
            assert "Welcome" in label
            assert "0:00–10:00" in label
            prompt = doc.to_prompt_string()
            assert prompt.startswith("[Source: ")
            assert "[0:10] hello students" in prompt

            indexed = doc.to_prompt_string(index=3)
            assert indexed.startswith("[3] [Source: "), indexed
            assert "[0:10] hello students" in indexed
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_recent_window_returns_trailing_segments_up_to_head() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            content, asset = await _create_lecture(session, course=course, title="Lecture A")

            session.add(
                VideoChapter(
                    video_asset_id=asset.id,
                    language_code="en",
                    chapter_index=0,
                    start_sec=0.0,
                    end_sec=600.0,
                    title="Intro Chapter",
                    model_id="test-chapter-model",
                    description=None,
                )
            )
            session.add_all(
                [
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=0.0,
                        end_sec=30.0,
                        text="way before the window",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=200.0,
                        end_sec=230.0,
                        text="just before now",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=230.0,
                        end_sec=260.0,
                        text="moments ago",
                        language_code="en",
                    ),
                    TranscriptSegment(
                        course_id=course.id,
                        video_asset_id=asset.id,
                        start_sec=400.0,
                        end_sec=430.0,
                        text="not yet watched",
                        language_code="en",
                    ),
                ]
            )
            await session.commit()

            info = await build_course_info(db=session, course=course)

            # Watching at 4:20 (260s), lookback 120s -> window [140, 260].
            docs = await retrieve_recent_window(
                db=session,
                course_info=info,
                lecture_slug="L1",
                head_sec=260.0,
                lookback_sec=120.0,
            )
            assert len(docs) == 1
            doc = docs[0]
            assert doc.lecture_id == asset.id
            assert doc.content_id == content.id
            assert doc.chapter_title == "Intro Chapter"  # chapter at head
            assert "just before now" in doc.text
            assert "moments ago" in doc.text
            assert "way before the window" not in doc.text  # outside lookback
            assert "not yet watched" not in doc.text  # after the head
            assert doc.start_sec == pytest.approx(200.0)
            assert doc.end_sec == pytest.approx(260.0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_recent_window_empty_cases() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            _, course = await _create_user_and_course(session)
            _, asset = await _create_lecture(session, course=course, title="Lecture A")
            _, pending = await _create_lecture(
                session, course=course, title="Pending", transcript_ready=False
            )

            session.add(
                TranscriptSegment(
                    course_id=course.id,
                    video_asset_id=asset.id,
                    start_sec=100.0,
                    end_sec=130.0,
                    text="some content",
                    language_code="en",
                )
            )
            await session.commit()

            info = await build_course_info(db=session, course=course)

            async def call(slug, head, lookback=120.0):
                return await retrieve_recent_window(
                    db=session,
                    course_info=info,
                    lecture_slug=slug,
                    head_sec=head,
                    lookback_sec=lookback,
                )

            # No playback head -> empty (soft-scope still applies elsewhere).
            assert await call("L1", None) == []
            # Negative head -> empty.
            assert await call("L1", -5.0) == []
            # head at 0 -> empty window -> empty.
            assert await call("L1", 0.0) == []
            # Unknown / malformed slug -> empty.
            assert await call("L99", 120.0) == []
            assert await call("garbage", 120.0) == []
            # Not-ready lecture (L2) -> empty even though it exists.
            assert info.lecture_by_slug("L2").id == pending.id
            assert await call("L2", 120.0) == []
            # No segments in the trailing window -> empty.
            assert await call("L1", 1000.0, 60.0) == []
    finally:
        await engine.dispose()
