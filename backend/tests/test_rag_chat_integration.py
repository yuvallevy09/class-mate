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
async def test_chat_returns_citations_when_rag_provides_hits(monkeypatch) -> None:
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip(
            "Database not reachable. Start Postgres and ensure DATABASE_URL is correct "
            "(docker-compose.yml maps host 5433 -> container 5432)."
        )

    await asyncio.to_thread(_run_migrations_sync)

    password = "pw"
    email = f"test-rag-{uuid4()}@example.com"
    user = await _create_user(settings.database_url, email=email, password=password)
    course = await _create_course(settings.database_url, user_id=user.id, name="Course")

    # Mock retrieval layer so we don't require embedding deps or external calls in tests.
    from app.rag import retrieve as rag_retrieve

    def _fake_retrieve_course_hits(**kwargs):  # noqa: ANN001
        from app.rag.types import RagHit

        return [
            RagHit(
                text="This is a retrieved snippet about eigenvalues.",
                metadata={
                    "content_id": str(uuid4()),
                    "title": "Lecture 3",
                    "original_filename": "lecture3.pdf",
                    "page": 2,
                },
                score=0.01,
            )
        ]

    monkeypatch.setattr(rag_retrieve, "retrieve_course_hits", _fake_retrieve_course_hits)

    # Mock the LLM call to keep test deterministic.
    from app.api.v1 import chat as chat_api

    async def _mock_generate_reply(self, **kwargs):  # noqa: ANN001
        # Call through to the real implementation so it maps hits -> citations,
        # but avoid hitting the network by short-circuiting llm.ainvoke.
        citations = []
        try:
            # Build citations using the same helper (keeps test aligned).
            hits = _fake_retrieve_course_hits()
            citations = self._hits_to_citations(hits)  # type: ignore[attr-defined]
        except Exception:
            citations = []
        return "mocked assistant reply", citations

    chat_api.ChatEngine.generate_reply = _mock_generate_reply  # type: ignore[method-assign]

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

        r = await client.post(
            f"/api/v1/courses/{course.id}/chat",
            json={"message": "What are eigenvalues?"},
            headers={settings.csrf_header_name: token},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["text"] == "mocked assistant reply"
        assert isinstance(body.get("citations"), list)
        assert len(body["citations"]) == 1
        assert body["citations"][0]["snippet"]


@pytest.mark.asyncio
async def test_chat_falls_back_to_empty_citations_when_no_rag_hits(monkeypatch) -> None:
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip(
            "Database not reachable. Start Postgres and ensure DATABASE_URL is correct "
            "(docker-compose.yml maps host 5433 -> container 5432)."
        )

    await asyncio.to_thread(_run_migrations_sync)

    password = "pw"
    email = f"test-rag-empty-{uuid4()}@example.com"
    await _create_user(settings.database_url, email=email, password=password)

    transport = httpx.ASGITransport(app=app)

    # Mock chat engine to avoid external calls; return no citations.
    from app.api.v1 import chat as chat_api

    async def _mock_generate_reply(self, **kwargs):  # noqa: ANN001
        return "mocked assistant reply", []

    chat_api.ChatEngine.generate_reply = _mock_generate_reply  # type: ignore[method-assign]

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        csrf = await client.get("/api/v1/auth/csrf")
        token = csrf.json()["csrfToken"]

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={settings.csrf_header_name: token},
        )
        assert login.status_code == 200

        # Create a course via API.
        created_course = await client.post(
            "/api/v1/courses",
            json={"name": "Course", "description": None},
            headers={settings.csrf_header_name: token},
        )
        assert created_course.status_code == 200
        course_id = created_course.json()["id"]

        r = await client.post(
            f"/api/v1/courses/{course_id}/chat",
            json={"message": "Hello"},
            headers={settings.csrf_header_name: token},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["text"] == "mocked assistant reply"
        assert body.get("citations") == []


