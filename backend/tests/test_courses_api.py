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


@pytest.mark.asyncio
async def test_courses_auth_and_ownership() -> None:
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip(
            "Database not reachable. Start Postgres and ensure DATABASE_URL is correct "
            "(docker-compose.yml maps host 5433 -> container 5432)."
        )

    # Ensure schema exists (includes courses).
    await asyncio.to_thread(_run_migrations_sync)

    password = "pw"
    email_a = f"test-a-{uuid4()}@example.com"
    email_b = f"test-b-{uuid4()}@example.com"
    user_a = await _create_user(settings.database_url, email=email_a, password=password)
    user_b = await _create_user(settings.database_url, email=email_b, password=password)

    transport = httpx.ASGITransport(app=app)

    # Unauthenticated should be rejected.
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
        r = await anon.get("/api/v1/courses")
        assert r.status_code == 401

    # Authenticated user A can create + list.
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        csrf = await client.get("/api/v1/auth/csrf")
        token = csrf.json()["csrfToken"]

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email_a, "password": password},
            headers={settings.csrf_header_name: token},
        )
        assert login.status_code == 200

        created = await client.post(
            "/api/v1/courses",
            json={"name": "Intro to Physics", "description": "Kinematics and dynamics"},
            headers={settings.csrf_header_name: token},
        )
        assert created.status_code == 200
        created_data = created.json()
        assert created_data["name"] == "Intro to Physics"
        assert created_data["description"] == "Kinematics and dynamics"
        assert created_data["id"]

        listed = await client.get("/api/v1/courses")
        assert listed.status_code == 200
        items = listed.json()
        assert any(c["id"] == created_data["id"] for c in items)

        # Create a course owned by user B directly in DB and ensure A can't access/delete it.
        b_course = await _create_course(settings.database_url, user_id=user_b.id, name="B course")

        forbidden_get = await client.get(f"/api/v1/courses/{b_course.id}")
        assert forbidden_get.status_code == 404

        forbidden_delete = await client.delete(
            f"/api/v1/courses/{b_course.id}",
            headers={settings.csrf_header_name: token},
        )
        assert forbidden_delete.status_code == 404


