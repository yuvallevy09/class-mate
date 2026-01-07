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
            user = User(email=email, hashed_password=hash_password(password))
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_course_name_is_required() -> None:
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip(
            "Database not reachable. Start Postgres and ensure DATABASE_URL is correct "
            "(docker-compose.yml maps host 5433 -> container 5432)."
        )

    await asyncio.to_thread(_run_migrations_sync)

    password = "pw"
    email = f"test-{uuid4()}@example.com"
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

        # Whitespace-only name should be rejected by request validation.
        created = await client.post(
            "/api/v1/courses",
            json={"name": "   ", "description": "x"},
            headers={settings.csrf_header_name: token},
        )
        assert created.status_code == 422


@pytest.mark.asyncio
async def test_course_content_title_and_category_required_and_s3_guard(monkeypatch) -> None:
    settings = get_settings()
    # Simulate "S3 not configured" regardless of a local `.env` by overriding the FastAPI
    # dependency used by the route (`Depends(get_settings)`).
    s3_disabled_settings = settings.model_copy(update={"s3_bucket": None})
    app.dependency_overrides[get_settings] = lambda: s3_disabled_settings

    try:
        if not await _can_connect(settings.database_url):
            pytest.skip(
                "Database not reachable. Start Postgres and ensure DATABASE_URL is correct "
                "(docker-compose.yml maps host 5433 -> container 5432)."
            )

        await asyncio.to_thread(_run_migrations_sync)

        password = "pw"
        email = f"test-{uuid4()}@example.com"
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

            course = await client.post(
                "/api/v1/courses",
                json={"name": "Course", "description": None},
                headers={settings.csrf_header_name: token},
            )
            assert course.status_code == 200
            course_id = course.json()["id"]

            missing_title = await client.post(
                f"/api/v1/courses/{course_id}/contents",
                json={"category": "notes", "title": "   "},
                headers={settings.csrf_header_name: token},
            )
            assert missing_title.status_code == 422

            missing_category = await client.post(
                f"/api/v1/courses/{course_id}/contents",
                json={"category": "   ", "title": "Hello"},
                headers={settings.csrf_header_name: token},
            )
            assert missing_category.status_code == 422

            invalid_category = await client.post(
                f"/api/v1/courses/{course_id}/contents",
                json={"category": "past_exams", "title": "Old label"},
                headers={settings.csrf_header_name: token},
            )
            assert invalid_category.status_code == 422

            # Without S3 configured, attaching a file should be rejected.
            with_file = await client.post(
                f"/api/v1/courses/{course_id}/contents",
                json={
                    "category": "notes",
                    "title": "File note",
                    "file_key": "users/1/courses/x/file.txt",
                    "original_filename": "file.txt",
                    "mime_type": "text/plain",
                    "size_bytes": 1,
                },
                headers={settings.csrf_header_name: token},
            )
            assert with_file.status_code == 501

            # But creating metadata-only content should still work with no S3 config.
            ok = await client.post(
                f"/api/v1/courses/{course_id}/contents",
                json={"category": "notes", "title": "Plain note", "description": "hi"},
                headers={settings.csrf_header_name: token},
            )
            assert ok.status_code == 200
    finally:
        app.dependency_overrides.pop(get_settings, None)


