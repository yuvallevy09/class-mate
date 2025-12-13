from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import get_settings


@lru_cache(maxsize=1)
def get_engine():
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_session_maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    SessionLocal = get_session_maker()
    async with SessionLocal() as session:
        yield session


