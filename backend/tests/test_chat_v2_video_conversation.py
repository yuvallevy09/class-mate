"""Per-video conversation persistence (chat-v2).

Authed httpx against the real app + live Postgres (skips if unreachable). The
TeachingAssistant is replaced with a fake `aforward` (no LLM). Verifies that a
chat started from the video player tags its conversation with the watched
lecture, that course chat stays untagged, that an off-course asset id degrades
to untagged, and that the list endpoint filters by `video_asset_id`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai.teaching_assistant import TeachingAssistantResult, get_teaching_assistant
from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.course_content import CourseContent
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


async def _create_user(database_url: str, *, email: str, password: str) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            session.add(User(email=email, hashed_password=hash_password(password), display_name="T"))
            await session.commit()
    finally:
        await engine.dispose()


async def _create_video_asset(database_url: str, *, course_id: UUID) -> UUID:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as s:
            content = CourseContent(
                course_id=course_id, category="media", title="Lecture",
                description=None, file_key=f"k-{uuid4()}",
                original_filename="lec.mp4", mime_type="video/mp4",
            )
            s.add(content)
            await s.commit()
            await s.refresh(content)
            asset = VideoAsset(
                course_id=course_id, content_id=content.id,
                source_file_key=content.file_key or "",
                original_filename=content.original_filename, mime_type="video/mp4",
            )
            s.add(asset)
            await s.commit()
            await s.refresh(asset)
            return asset.id
    finally:
        await engine.dispose()


class _FakeTA:
    async def aforward(self, *, db, course_info, conversation_history, user_query, viewing=None):  # noqa: ARG002
        return TeachingAssistantResult(answer="ok", route="answer")


async def _login_and_make_course(client, settings, email, password) -> tuple[str, str]:
    csrf = await client.get("/api/v1/auth/csrf")
    token = csrf.json()["csrfToken"]
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={settings.csrf_header_name: token},
    )
    assert login.status_code == 200
    course = await client.post(
        "/api/v1/courses",
        json={"name": "Course", "description": None},
        headers={settings.csrf_header_name: token},
    )
    assert course.status_code == 200
    return token, course.json()["id"]


@pytest.mark.asyncio
async def test_video_chat_tags_conversation_and_list_filters(monkeypatch) -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")
    await asyncio.to_thread(_run_migrations_sync)

    email, password = f"vid-{uuid4()}@e.com", "pw"
    await _create_user(settings.database_url, email=email, password=password)

    from app.api.v1 import chat_v2 as chat_v2_mod

    async def _no_title(**kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr(chat_v2_mod, "_maybe_generate_title", _no_title)
    app.dependency_overrides[get_teaching_assistant] = lambda: _FakeTA()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            token, course_id = await _login_and_make_course(c, settings, email, password)
            asset_id = await _create_video_asset(settings.database_url, course_id=UUID(course_id))
            hdr = {settings.csrf_header_name: token}

            # 1. Video chat -> conversation tagged with the watched lecture.
            r = await c.post(
                f"/api/v1/courses/{course_id}/chat-v2",
                json={"message": "explain this", "watchingVideoAssetId": str(asset_id)},
                headers=hdr,
            )
            assert r.status_code == 200
            video_convo_id = r.json()["conversationId"]

            # 2. Plain course chat -> untagged conversation.
            r2 = await c.post(
                f"/api/v1/courses/{course_id}/chat-v2",
                json={"message": "general question"},
                headers=hdr,
            )
            assert r2.status_code == 200
            course_convo_id = r2.json()["conversationId"]
            assert course_convo_id != video_convo_id

            # 3. Off-course asset id degrades to untagged (no FK violation, no error).
            r3 = await c.post(
                f"/api/v1/courses/{course_id}/chat-v2",
                json={"message": "weird", "watchingVideoAssetId": str(uuid4())},
                headers=hdr,
            )
            assert r3.status_code == 200
            foreign_convo_id = r3.json()["conversationId"]

            # Bare list = course-level conversations only; the video-tagged thread
            # is excluded (it lives on its lecture page). The off-course asset
            # degraded to untagged, so it counts as course-level.
            course_level = (await c.get(f"/api/v1/courses/{course_id}/conversations")).json()
            ids = {cv["id"] for cv in course_level}
            assert video_convo_id not in ids
            assert ids == {course_convo_id, foreign_convo_id}
            assert all(cv["video_asset_id"] is None for cv in course_level)

            # Filtered list: only the tagged conversation for this lecture.
            filtered = (
                await c.get(
                    f"/api/v1/courses/{course_id}/conversations",
                    params={"video_asset_id": str(asset_id)},
                )
            ).json()
            assert [cv["id"] for cv in filtered] == [video_convo_id]

            # Filtering by an unrelated asset id returns nothing.
            empty = (
                await c.get(
                    f"/api/v1/courses/{course_id}/conversations",
                    params={"video_asset_id": str(uuid4())},
                )
            ).json()
            assert empty == []
    finally:
        app.dependency_overrides.pop(get_teaching_assistant, None)
