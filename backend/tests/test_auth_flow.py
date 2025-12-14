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


def _run_migrations_sync() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


@pytest.mark.asyncio
async def test_login_refresh_rotation_logout_flow() -> None:
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip(
            "Database not reachable. Start Postgres and ensure DATABASE_URL is correct "
            "(docker-compose.yml maps host 5433 -> container 5432)."
        )

    # Ensure schema exists.
    await asyncio.to_thread(_run_migrations_sync)

    password = "pw"
    email = f"test-{uuid4()}@example.com"
    user = await _create_user(settings.database_url, email=email, password=password)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert resp.status_code == 200

        set_cookie_headers = resp.headers.get_list("set-cookie")
        assert any(settings.access_cookie_name in h for h in set_cookie_headers)
        assert any(settings.refresh_cookie_name in h for h in set_cookie_headers)

        old_refresh = client.cookies.get(settings.refresh_cookie_name)
        assert old_refresh is not None

        me = await client.get("/api/v1/users/me")
        assert me.status_code == 200
        data = me.json()
        assert data["id"] == user.id
        assert data["email"] == email

        refresh1 = await client.post("/api/v1/auth/refresh")
        assert refresh1.status_code == 200

        new_refresh = client.cookies.get(settings.refresh_cookie_name)
        assert new_refresh is not None
        assert new_refresh != old_refresh

        # Replay old refresh token should fail (rotation).
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as replay_client:
            replay_client.cookies.set(
                settings.refresh_cookie_name,
                old_refresh,
                domain="test",
                path="/",
            )
            replay = await replay_client.post(
                "/api/v1/auth/refresh",
            )
        assert replay.status_code == 401

        # Logout should clear cookies.
        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 200

        after_logout = await client.get("/api/v1/users/me")
        assert after_logout.status_code == 401
