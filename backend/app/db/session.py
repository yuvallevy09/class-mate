from __future__ import annotations

from collections.abc import AsyncGenerator
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import get_settings


_engines_by_loop: dict[int, object] = {}
_sessionmakers_by_loop: dict[int, async_sessionmaker[AsyncSession]] = {}


def _loop_cache_key() -> int:
    # Async DB drivers (asyncpg) are tied to the event loop. Caching a single
    # engine across multiple loops (e.g. pytest-asyncio) causes runtime errors.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    return id(loop)


def get_engine():
    key = _loop_cache_key()
    engine = _engines_by_loop.get(key)
    if engine is None:
        settings = get_settings()
        engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
        )
        _engines_by_loop[key] = engine
    return engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    key = _loop_cache_key()
    maker = _sessionmakers_by_loop.get(key)
    if maker is None:
        maker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
        _sessionmakers_by_loop[key] = maker
    return maker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    SessionLocal = get_session_maker()
    async with SessionLocal() as session:
        yield session


