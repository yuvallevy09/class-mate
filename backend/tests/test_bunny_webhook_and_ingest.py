from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.course import Course
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.main import app


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


async def _create_user(database_url: str, *, email: str, password: str) -> User:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            user = User(email=email, hashed_password=hash_password(password), display_name="Test")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user
    finally:
        await engine.dispose()


async def _create_course(database_url: str, *, user_id: int, name: str) -> Course:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            course = Course(user_id=user_id, name=name, description=None)
            session.add(course)
            await session.commit()
            await session.refresh(course)
            return course
    finally:
        await engine.dispose()


async def _create_video_asset(database_url: str, *, course_id, video_guid: str, pull_zone_url: str) -> VideoAsset:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            asset = VideoAsset(
                course_id=course_id,
                content_id=None,
                provider="bunny",
                video_library_id=123,
                video_guid=video_guid,
                pull_zone_url=pull_zone_url,
                status="queued",
                captions_language_code="en",
            )
            session.add(asset)
            await session.commit()
            await session.refresh(asset)
            return asset
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_is_csrf_exempt_and_enforces_secret(monkeypatch) -> None:
    monkeypatch.setenv("BUNNY_WEBHOOK_SECRET", "secret123")
    get_settings.cache_clear()
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres and ensure DATABASE_URL is correct.")

    await asyncio.to_thread(_run_migrations_sync)

    # Create user/course/asset so webhook can find it.
    user = await _create_user(settings.database_url, email=f"t-{uuid4()}@e.com", password="pw")
    course = await _create_course(settings.database_url, user_id=user.id, name="Course")
    guid = f"guid-{uuid4()}"
    await _create_video_asset(settings.database_url, course_id=course.id, video_guid=guid, pull_zone_url="zone1")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # No CSRF header/cookie should be required for webhook.
        r_bad = await client.post(
            "/api/webhooks/bunny/stream/wrong",
            json={"VideoLibraryId": 123, "VideoGuid": guid, "Status": 3},
        )
        assert r_bad.status_code == 401

        r_ok = await client.post(
            "/api/webhooks/bunny/stream/secret123",
            json={"VideoLibraryId": 123, "VideoGuid": guid, "Status": 3},
        )
        assert r_ok.status_code == 200
        assert r_ok.json().get("ok") is True


@pytest.mark.asyncio
async def test_ingest_transcript_persists_segments(monkeypatch) -> None:
    monkeypatch.setenv("BUNNY_WEBHOOK_SECRET", "secret123")
    get_settings.cache_clear()
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres and ensure DATABASE_URL is correct.")

    await asyncio.to_thread(_run_migrations_sync)

    user = await _create_user(settings.database_url, email=f"t2-{uuid4()}@e.com", password="pw")
    course = await _create_course(settings.database_url, user_id=user.id, name="Course")
    asset = await _create_video_asset(settings.database_url, course_id=course.id, video_guid="guid-2", pull_zone_url="zone2")

    # Mock VTT fetch to keep test deterministic.
    import app.bunny.ingest as ingest

    sample_vtt = """WEBVTT

00:00:00.000 --> 00:00:01.000
Hello world

00:00:01.000 --> 00:00:03.000
Second cue
"""

    async def _fake_fetch_text(url: str, *, timeout_seconds: float = 15.0) -> str:  # noqa: ANN001
        assert "zone2" in url
        assert "guid-2" in url
        return sample_vtt

    monkeypatch.setattr(ingest, "_fetch_text", _fake_fetch_text)

    # Call the ingestion worker directly (avoid relying on BackgroundTasks timing).
    await ingest.ingest_bunny_transcript_for_video_asset(video_asset_id=asset.id)

    # Verify segments exist.
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            res = await session.execute(
                text("SELECT count(*) FROM transcript_segments WHERE video_asset_id = :vid").bindparams(vid=asset.id)
            )
            count = int(res.scalar_one())
            assert count >= 1

            # Verify ORM view also works.
            segs = (
                await session.execute(
                    select(TranscriptSegment).where(TranscriptSegment.video_asset_id == asset.id)
                )
            ).scalars().all()
            assert any("Hello" in s.text for s in segs)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_register_and_list_segments_and_embed_url(monkeypatch) -> None:
    # Configure webhook secret only so settings initialize deterministically.
    monkeypatch.setenv("BUNNY_WEBHOOK_SECRET", "secret123")
    get_settings.cache_clear()
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres and ensure DATABASE_URL is correct.")

    await asyncio.to_thread(_run_migrations_sync)

    # Auth bootstrap (cookie auth + CSRF).
    password = "pw"
    email = f"t3-{uuid4()}@e.com"
    await _create_user(settings.database_url, email=email, password=password)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        csrf = await client.get("/api/v1/auth/csrf")
        token = csrf.json()["csrfToken"]

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={settings.csrf_header_name: token},
        )
        assert login.status_code == 200

        created_course = await client.post(
            "/api/v1/courses",
            json={"name": "Course", "description": None},
            headers={settings.csrf_header_name: token},
        )
        assert created_course.status_code == 200
        course_id = created_course.json()["id"]

        guid = f"guid-{uuid4()}"
        reg = await client.post(
            f"/api/v1/courses/{course_id}/videos/bunny/{guid}/register",
            json={
                "videoLibraryId": 123,
                "pullZoneUrl": "zone9",
                "captionsLanguageCode": "en",
                "contentId": None,
            },
            headers={settings.csrf_header_name: token},
        )
        assert reg.status_code == 200
        asset_id = reg.json()["id"]
        assert reg.json()["videoGuid"] == guid
        assert "iframe.mediadelivery.net/embed/123" in (reg.json().get("embedUrl") or "")

        # Get asset by guid.
        got = await client.get(
            f"/api/v1/courses/{course_id}/videos/bunny/{guid}",
            headers={settings.csrf_header_name: token},
        )
        assert got.status_code == 200
        assert got.json()["id"] == asset_id

        # List assets for course.
        listed = await client.get(
            f"/api/v1/courses/{course_id}/videos?provider=bunny&limit=50&offset=0",
            headers={settings.csrf_header_name: token},
        )
        assert listed.status_code == 200
        ids = [a.get("id") for a in listed.json() if isinstance(a, dict)]
        assert asset_id in ids

        # Paged contract (items + total).
        page = await client.get(
            f"/api/v1/courses/{course_id}/videos/page?provider=bunny&limit=50&offset=0",
            headers={settings.csrf_header_name: token},
        )
        assert page.status_code == 200
        pj = page.json()
        assert isinstance(pj.get("items"), list)
        assert isinstance(pj.get("total"), int)
        assert asset_id in [a.get("id") for a in pj["items"] if isinstance(a, dict)]

        # Mock VTT fetch and ingest directly (avoid relying on BackgroundTasks ordering).
        import app.bunny.ingest as ingest

        sample_vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
Alpha

00:00:02.000 --> 00:00:04.000
Beta
"""

        async def _fake_fetch_text(url: str, *, timeout_seconds: float = 15.0) -> str:  # noqa: ANN001
            assert "zone9" in url
            assert guid in url
            return sample_vtt

        monkeypatch.setattr(ingest, "_fetch_text", _fake_fetch_text)
        await ingest.ingest_bunny_transcript_for_video_asset(video_asset_id=UUID(asset_id))

        segs = await client.get(
            f"/api/v1/courses/{course_id}/videos/bunny/{guid}/segments?language_code=en&limit=50",
            headers={settings.csrf_header_name: token},
        )
        assert segs.status_code == 200
        body = segs.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        assert any("Alpha" in (s.get("text") or "") for s in body)

        # Embed helper with t=
        emb = await client.get(
            f"/api/v1/courses/{course_id}/videos/bunny/{guid}/embed?t=50",
            headers={settings.csrf_header_name: token},
        )
        assert emb.status_code == 200
        assert "t=50" in emb.json()["url"]


