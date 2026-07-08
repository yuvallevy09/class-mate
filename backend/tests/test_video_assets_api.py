from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
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
from app.main import app
import app.api.v1.video_assets as video_assets_api
import app.api.v1.course_contents as course_contents_api


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
            user = User(email=email, hashed_password=hash_password(password))
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user
    finally:
        await engine.dispose()


async def _set_ai_description(database_url: str, *, video_asset_id: str, description: str) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("UPDATE video_assets SET ai_description = :d WHERE id = :id"),
                {"d": description, "id": video_asset_id},
            )
            await conn.commit()
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


@pytest.mark.asyncio
async def test_video_assets_auth_and_ownership(monkeypatch) -> None:
    # Ensure settings pick up S3_BUCKET for this test.
    monkeypatch.setenv("S3_BUCKET", "classmate")
    # Dummy Runpod config so /transcribe can proceed past config validation.
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "test-endpoint")
    get_settings.cache_clear()
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip(
            "Database not reachable. Start Postgres and ensure DATABASE_URL is correct "
            "(docker-compose.yml maps host 5433 -> container 5432)."
        )

    await asyncio.to_thread(_run_migrations_sync)

    password = "pw"
    email_a = f"test-a-{uuid4()}@example.com"
    email_b = f"test-b-{uuid4()}@example.com"
    user_a = await _create_user(settings.database_url, email=email_a, password=password)
    user_b = await _create_user(settings.database_url, email=email_b, password=password)
    course_a = await _create_course(settings.database_url, user_id=user_a.id, name="A course")
    course_b = await _create_course(settings.database_url, user_id=user_b.id, name="B course")

    transport = httpx.ASGITransport(app=app)

    # Unauthenticated should be rejected.
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
        r = await anon.get(f"/api/v1/courses/{course_a.id}/video-assets")
        assert r.status_code == 401

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        csrf = await client.get("/api/v1/auth/csrf")
        token = csrf.json()["csrfToken"]

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email_a, "password": password},
            headers={settings.csrf_header_name: token},
        )
        assert login.status_code == 200

        # CSRF required for finalize.
        missing_csrf_finalize = await client.post(
            f"/api/v1/courses/{course_a.id}/videos",
            json={
                "title": "Lecture 1",
                "description": "Intro",
                "source_file_key": f"users/{user_a.id}/courses/{course_a.id}/{uuid4()}_video.mp4",
                "original_filename": "video.mp4",
                "mime_type": "video/mp4",
                "size_bytes": 123,
                "kickoffTranscription": False,
            },
        )
        assert missing_csrf_finalize.status_code == 403

        created = await client.post(
            f"/api/v1/courses/{course_a.id}/videos",
            json={
                "title": "Lecture 1",
                "description": "Intro",
                "source_file_key": f"users/{user_a.id}/courses/{course_a.id}/{uuid4()}_video.mp4",
                "original_filename": "video.mp4",
                "mime_type": "video/mp4",
                "size_bytes": 123,
                "kickoffTranscription": False,
            },
            headers={settings.csrf_header_name: token},
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert created_payload["content"]["course_id"] == str(course_a.id)
        assert created_payload["content"]["category"] == "media"
        asset = created_payload["videoAsset"]
        assert asset["course_id"] == str(course_a.id)
        assert asset["provider"] == "local"
        assert asset["status"] == "uploaded"
        assert asset["source_file_key"].startswith(f"users/{user_a.id}/")
        assert asset["content_id"] == created_payload["content"]["id"]

        listed = await client.get(f"/api/v1/courses/{course_a.id}/video-assets")
        assert listed.status_code == 200
        items = listed.json()
        assert any(i["id"] == asset["id"] for i in items)

        # ai_description is exposed on the list payload: None until generated, then round-trips.
        listed_item = next(i for i in items if i["id"] == asset["id"])
        assert listed_item["ai_description"] is None

        await _set_ai_description(
            settings.database_url,
            video_asset_id=asset["id"],
            description="Introduces vectors, span, and linear independence.",
        )
        relisted = await client.get(f"/api/v1/courses/{course_a.id}/video-assets")
        assert relisted.status_code == 200
        relisted_item = next(i for i in relisted.json() if i["id"] == asset["id"])
        assert relisted_item["ai_description"] == "Introduces vectors, span, and linear independence."

        got = await client.get(f"/api/v1/video-assets/{asset['id']}")
        assert got.status_code == 200
        assert got.json()["id"] == asset["id"]

        # Idempotency: finalizing the same source_file_key again should return the existing asset (not error).
        dup = await client.post(
            f"/api/v1/courses/{course_a.id}/videos",
            json={
                "title": "Lecture 1 (dup)",
                "description": "Intro (dup)",
                "source_file_key": asset["source_file_key"],
                "original_filename": "video.mp4",
                "mime_type": "video/mp4",
                "size_bytes": 123,
                "kickoffTranscription": False,
            },
            headers={settings.csrf_header_name: token},
        )
        assert dup.status_code == 200
        assert dup.json()["videoAsset"]["id"] == asset["id"]

        # User A must not be able to list B's assets (404 course not found under ownership).
        forbidden_list = await client.get(f"/api/v1/courses/{course_b.id}/video-assets")
        assert forbidden_list.status_code == 404

        # User A must not be able to fetch a random UUID.
        missing = await client.get(f"/api/v1/video-assets/{uuid4()}")
        assert missing.status_code == 404

        # Start transcription should flip status to processing (PR3.2 behavior).
        async def _noop_transcribe_video_asset(*, video_asset_id, requested_language=None):
            return None

        monkeypatch.setattr(video_assets_api, "transcribe_video_asset", _noop_transcribe_video_asset)

        start = await client.post(
            f"/api/v1/video-assets/{asset['id']}/transcribe",
            json={"force": False},
            headers={settings.csrf_header_name: token},
        )
        assert start.status_code == 200
        start_payload = start.json()
        assert start_payload["ok"] is True
        assert start_payload["video_asset_id"] == asset["id"]
        assert start_payload["status"] == "extracting_audio"

        # Second call should be idempotent and stay processing.
        start2 = await client.post(
            f"/api/v1/video-assets/{asset['id']}/transcribe",
            json={"force": False},
            headers={settings.csrf_header_name: token},
        )
        assert start2.status_code == 200
        assert start2.json()["status"] == "extracting_audio"

        # Deleting the content item should cascade-delete the video asset (FK ON DELETE CASCADE).
        class _StubS3:
            def delete_object(self, *, Bucket, Key):
                return {}

        monkeypatch.setattr(course_contents_api, "s3_client", lambda _settings: _StubS3())

        deleted = await client.delete(
            f"/api/v1/contents/{created_payload['content']['id']}",
            headers={settings.csrf_header_name: token},
        )
        assert deleted.status_code == 204

        gone = await client.get(f"/api/v1/video-assets/{asset['id']}")
        assert gone.status_code == 404


async def _seed_videos_with_same_created_at(
    database_url: str, *, course_id: UUID, count: int
) -> list[UUID]:
    """
    Insert `count` media contents + linked video assets that all share the exact
    same created_at, mirroring a burst of near-simultaneous uploads where
    func.now() (transaction-start time) ties. Returns the content ids created.
    """
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    tied = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    content_ids: list[UUID] = []
    try:
        async with SessionLocal() as session:
            for i in range(count):
                key = f"users/1/courses/{course_id}/{uuid4()}_video.mp4"
                content = CourseContent(
                    course_id=course_id,
                    category="media",
                    title=f"Lecture {i}",
                    file_key=key,
                    original_filename="video.mp4",
                    mime_type="video/mp4",
                    created_at=tied,
                )
                session.add(content)
                await session.flush()
                asset = VideoAsset(
                    course_id=course_id,
                    content_id=content.id,
                    provider="local",
                    status="uploaded",
                    source_file_key=key,
                    original_filename="video.mp4",
                    mime_type="video/mp4",
                    created_at=tied,
                )
                session.add(asset)
                content_ids.append(content.id)
            await session.commit()
    finally:
        await engine.dispose()
    return content_ids


@pytest.mark.asyncio
async def test_list_order_is_deterministic_for_tied_created_at(monkeypatch) -> None:
    """
    Regression: uploading many videos at once mixed up their order until a refresh.

    Root cause was `ORDER BY created_at DESC` with no unique tie-breaker: created_at
    is populated by func.now() (transaction-start time), so a burst of uploads can
    share a timestamp and PostgreSQL then returns tied rows in arbitrary heap order,
    which can differ between calls. Both list endpoints now tie-break on id, so the
    order is stable across refetches.
    """
    monkeypatch.setenv("S3_BUCKET", "classmate")
    get_settings.cache_clear()
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip(
            "Database not reachable. Start Postgres and ensure DATABASE_URL is correct "
            "(docker-compose.yml maps host 5433 -> container 5432)."
        )

    await asyncio.to_thread(_run_migrations_sync)

    password = "pw"
    email = f"order-{uuid4()}@example.com"
    user = await _create_user(settings.database_url, email=email, password=password)
    course = await _create_course(settings.database_url, user_id=user.id, name="Order course")

    content_ids = await _seed_videos_with_same_created_at(
        settings.database_url, course_id=course.id, count=6
    )
    # Expected deterministic order: created_at DESC, id DESC (all created_at tie here).
    expected_content_order = [str(cid) for cid in sorted(content_ids, reverse=True)]

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

        # Fetch the contents list twice; order must be identical and match the expected
        # (created_at DESC, id DESC) ordering — no reshuffling between refetches.
        orders: list[list[str]] = []
        for _ in range(2):
            res = await client.get(
                f"/api/v1/courses/{course.id}/contents", params={"category": "media"}
            )
            assert res.status_code == 200
            orders.append([item["id"] for item in res.json()])
        assert orders[0] == expected_content_order
        assert orders[0] == orders[1]

        # The video-assets list must be deterministic too (used for status badges).
        asset_orders: list[list[str]] = []
        for _ in range(2):
            res = await client.get(f"/api/v1/courses/{course.id}/video-assets")
            assert res.status_code == 200
            asset_orders.append([item["id"] for item in res.json()])
        assert asset_orders[0] == asset_orders[1]
        assert len(asset_orders[0]) == len(content_ids)


