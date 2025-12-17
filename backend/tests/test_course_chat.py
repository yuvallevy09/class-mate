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
from app.db.models.user import User
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


@pytest.mark.asyncio
async def test_course_chat_requires_csrf_and_auth_and_ownership_and_valid_message() -> None:
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip(
            "Database not reachable. Start Postgres and ensure DATABASE_URL is correct "
            "(docker-compose.yml maps host 5433 -> container 5432)."
        )

    await asyncio.to_thread(_run_migrations_sync)

    password = "pw"
    user1_email = f"test-chat-1-{uuid4()}@example.com"
    user2_email = f"test-chat-2-{uuid4()}@example.com"
    await _create_user(settings.database_url, email=user1_email, password=password)
    await _create_user(settings.database_url, email=user2_email, password=password)

    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c1:
        csrf = await c1.get("/api/v1/auth/csrf")
        token = csrf.json()["csrfToken"]

        # Without auth cookies, but with CSRF, should fail 401 (auth).
        unauth = await c1.post(
            f"/api/v1/courses/{uuid4()}/chat",
            json={"message": "hi", "conversationId": None},
            headers={settings.csrf_header_name: token},
        )
        assert unauth.status_code == 401

        # Login user1.
        login1 = await c1.post(
            "/api/v1/auth/login",
            json={"email": user1_email, "password": password},
            headers={settings.csrf_header_name: token},
        )
        assert login1.status_code == 200

        # Create a course as user1.
        course1 = await c1.post(
            "/api/v1/courses",
            json={"name": "Course 1", "description": None},
            headers={settings.csrf_header_name: token},
        )
        assert course1.status_code == 200
        course1_id = course1.json()["id"]

        # Missing CSRF should be blocked by middleware.
        missing_csrf = await c1.post(
            f"/api/v1/courses/{course1_id}/chat",
            json={"message": "hello"},
        )
        assert missing_csrf.status_code == 403

        ok = await c1.post(
            f"/api/v1/courses/{course1_id}/chat",
            json={"message": "hello", "conversationId": None},
            headers={settings.csrf_header_name: token},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert isinstance(body.get("text"), str) and body["text"]
        assert body.get("citations") == []
        assert isinstance(body.get("conversationId"), str) and body["conversationId"]

        convo_id = body["conversationId"]

        # Messages are persisted (user + assistant).
        msgs = await c1.get(f"/api/v1/conversations/{convo_id}/messages")
        assert msgs.status_code == 200
        messages = msgs.json()
        assert isinstance(messages, list) and len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello"
        assert messages[1]["role"] == "assistant"
        assert "(stub)" in messages[1]["content"]

        # Conversations are listed per course (most recent first).
        convos = await c1.get(f"/api/v1/courses/{course1_id}/conversations")
        assert convos.status_code == 200
        convos_body = convos.json()
        assert isinstance(convos_body, list) and len(convos_body) == 1
        assert convos_body[0]["id"] == convo_id

        # Deleting a conversation requires CSRF.
        missing_csrf_delete = await c1.delete(f"/api/v1/conversations/{convo_id}")
        assert missing_csrf_delete.status_code == 403

        deleted = await c1.delete(
            f"/api/v1/conversations/{convo_id}",
            headers={settings.csrf_header_name: token},
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True}

        # Messages should be gone (hidden as 404).
        msgs_after_delete = await c1.get(f"/api/v1/conversations/{convo_id}/messages")
        assert msgs_after_delete.status_code == 404

        # Conversation should disappear from course list.
        convos_after_delete = await c1.get(f"/api/v1/courses/{course1_id}/conversations")
        assert convos_after_delete.status_code == 200
        assert convos_after_delete.json() == []

        # Empty/whitespace message should be rejected by request validation.
        empty = await c1.post(
            f"/api/v1/courses/{course1_id}/chat",
            json={"message": "   "},
            headers={settings.csrf_header_name: token},
        )
        assert empty.status_code == 422

        too_long = await c1.post(
            f"/api/v1/courses/{course1_id}/chat",
            json={"message": "x" * 5000},
            headers={settings.csrf_header_name: token},
        )
        assert too_long.status_code == 422

    # Ownership: user2 creates a course; user1 cannot chat on it -> 404.
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c2:
        csrf2 = await c2.get("/api/v1/auth/csrf")
        token2 = csrf2.json()["csrfToken"]

        login2 = await c2.post(
            "/api/v1/auth/login",
            json={"email": user2_email, "password": password},
            headers={settings.csrf_header_name: token2},
        )
        assert login2.status_code == 200

        course2 = await c2.post(
            "/api/v1/courses",
            json={"name": "Course 2", "description": None},
            headers={settings.csrf_header_name: token2},
        )
        assert course2.status_code == 200
        course2_id = course2.json()["id"]

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c1_again:
        csrf3 = await c1_again.get("/api/v1/auth/csrf")
        token3 = csrf3.json()["csrfToken"]
        login1_again = await c1_again.post(
            "/api/v1/auth/login",
            json={"email": user1_email, "password": password},
            headers={settings.csrf_header_name: token3},
        )
        assert login1_again.status_code == 200

        forbidden = await c1_again.post(
            f"/api/v1/courses/{course2_id}/chat",
            json={"message": "hi"},
            headers={settings.csrf_header_name: token3},
        )
        assert forbidden.status_code == 404

    # Ownership: user1 cannot read user2 conversation messages.
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c2_again:
        csrf4 = await c2_again.get("/api/v1/auth/csrf")
        token4 = csrf4.json()["csrfToken"]
        login2_again = await c2_again.post(
            "/api/v1/auth/login",
            json={"email": user2_email, "password": password},
            headers={settings.csrf_header_name: token4},
        )
        assert login2_again.status_code == 200

        # Create a conversation on user2's course.
        chat2 = await c2_again.post(
            f"/api/v1/courses/{course2_id}/chat",
            json={"message": "secret"},
            headers={settings.csrf_header_name: token4},
        )
        assert chat2.status_code == 200
        convo2_id = chat2.json()["conversationId"]

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c1_third:
        csrf5 = await c1_third.get("/api/v1/auth/csrf")
        token5 = csrf5.json()["csrfToken"]
        login1_third = await c1_third.post(
            "/api/v1/auth/login",
            json={"email": user1_email, "password": password},
            headers={settings.csrf_header_name: token5},
        )
        assert login1_third.status_code == 200

        # Should be hidden as 404 (ownership).
        msgs_forbidden = await c1_third.get(f"/api/v1/conversations/{convo2_id}/messages")
        assert msgs_forbidden.status_code == 404
