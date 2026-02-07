from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

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
from app.db.models.video_chapter import VideoChapter
from app.main import app


def _run_migrations_sync() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


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


@pytest.mark.asyncio
async def test_course_chat_attaches_chapter_title_from_chapter_id(monkeypatch) -> None:
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    # Setup DB entities.
    await asyncio.to_thread(_run_migrations_sync)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        from app.api.v1 import chat as chat_api
        from app.schemas.chat import ChatCitation

        async with SessionLocal() as session:
            user = User(email=f"u-{uuid4()}@e.com", hashed_password=hash_password("pw"), display_name="T")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            course = Course(user_id=user.id, name="Course", description=None)
            session.add(course)
            await session.commit()
            await session.refresh(course)

            content = CourseContent(course_id=course.id, category="media", title="Lecture 1", description=None)
            session.add(content)
            await session.commit()
            await session.refresh(content)

            asset = VideoAsset(
                course_id=course.id,
                content_id=content.id,
                source_file_key="x",
                original_filename="lecture1.mp4",
                mime_type="video/mp4",
                size_bytes=123,
            )
            session.add(asset)
            await session.commit()
            await session.refresh(asset)

            chapter = VideoChapter(
                video_asset_id=asset.id,
                language_code="en",
                chapter_index=0,
                start_sec=0.0,
                end_sec=100.0,
                title="Full Lecture",
                description=None,
            )
            session.add(chapter)
            await session.commit()
            await session.refresh(chapter)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await client.get("/api/v1/auth/csrf")
            token = csrf.json()["csrfToken"]

            login = await client.post(
                "/api/v1/auth/login",
                json={"email": user.email, "password": "pw"},
                headers={settings.csrf_header_name: token},
            )
            assert login.status_code == 200

            # Call chat; patch citation to point to correct content_id + chapterId.
            async def _mock_generate_reply2(self, **kwargs):  # noqa: ANN001
                c = ChatCitation(
                    content_id=content.id,
                    title=None,
                    url=None,
                    snippet="snippet",
                    extra={"type": "video", "startSec": 0, "endSec": 10, "chapterId": str(chapter.id)},
                )
                return "reply [1]", [c]

            chat_api.ChatEngine.generate_reply = _mock_generate_reply2  # type: ignore[method-assign]

            r = await client.post(
                f"/api/v1/courses/{course.id}/chat",
                json={"message": "hi"},
                headers={settings.csrf_header_name: token},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["citations"]
            extra = body["citations"][0]["extra"]
            assert extra.get("chapterTitle") == "Full Lecture"
    finally:
        await engine.dispose()

