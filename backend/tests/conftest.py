"""Run the test suite against a dedicated `<dev-db>_test` database.

Tests (and the app code they exercise, including Alembic's env.py) resolve the
database from `get_settings().database_url`, so overriding DATABASE_URL here —
at conftest import, before any test module imports app code — isolates fixture
data from the dev database. Real env vars take precedence over backend/.env in
pydantic-settings, which is what makes the override stick.

The test database is created on first run and migrated to head once per
session; the `alembic upgrade head` calls some tests still make themselves stay
cheap no-ops after that. Set TEST_DATABASE_URL to use a different test DB.

Postgres being unreachable is not an error: DB-backed tests already detect that
and skip themselves, so the session fixture just declines to create/migrate.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _activate_test_database_url() -> str:
    from app.core.settings import get_settings

    url = (os.environ.get("TEST_DATABASE_URL") or "").strip()
    if not url:
        parts = urlsplit(get_settings().database_url)
        db_name = parts.path.lstrip("/")
        if not db_name.endswith("_test"):
            parts = parts._replace(path=f"/{db_name}_test")
        url = urlunsplit(parts)

    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    return url


# At import time so it precedes every test-module import of app code.
_TEST_DATABASE_URL = _activate_test_database_url()


async def _ensure_database_exists(url: str) -> bool:
    """Create the test database if missing. False when the server is unreachable."""
    import asyncpg

    parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://"))
    db_name = parts.path.lstrip("/")
    try:
        conn = await asyncpg.connect(urlunsplit(parts._replace(path="/postgres")))
    except Exception:
        return False
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
        return True
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> None:
    if not asyncio.run(_ensure_database_exists(_TEST_DATABASE_URL)):
        return

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    command.upgrade(cfg, "head")
