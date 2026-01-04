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
            user = User(email=email, hashed_password=hash_password(password))
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


async def _create_content(database_url: str, *, course_id, category: str, title: str, file_key: str) -> CourseContent:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            c = CourseContent(
                course_id=course_id,
                category=category,
                title=title,
                description=None,
                file_key=file_key,
                original_filename="test.txt",
                mime_type="text/plain",
                size_bytes=1,
            )
            session.add(c)
            await session.commit()
            await session.refresh(c)
            return c
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_content_deletes_s3_object(monkeypatch) -> None:
    # Ensure settings pick up S3_BUCKET for this test.
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
    email = f"test-{uuid4()}@example.com"
    user = await _create_user(settings.database_url, email=email, password=password)
    course = await _create_course(settings.database_url, user_id=user.id, name="Course")
    content = await _create_content(
        settings.database_url,
        course_id=course.id,
        category="notes",
        title="Note",
        file_key="users/1/courses/x/file.txt",
    )

    calls = []

    class _StubS3:
        def delete_object(self, *, Bucket, Key):
            calls.append((Bucket, Key))
            return {}

    import app.api.v1.course_contents as cc

    monkeypatch.setattr(cc, "_s3_client", lambda _settings: _StubS3())

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

        r = await client.delete(
            f"/api/v1/contents/{content.id}",
            headers={settings.csrf_header_name: token},
        )
        assert r.status_code == 204

    assert calls == [(settings.s3_bucket, content.file_key)]




