from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.chat_conversation import ChatConversation
from app.db.models.chat_message import ChatMessage
from app.db.models.course import Course
from app.db.models.user import User
from app.services.conversation_history import build_conversation_history


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


async def _create_conversation_with_messages(
    session,
    *,
    messages: list[tuple[str, str]],
) -> ChatConversation:
    user = User(
        email=f"u-{uuid4()}@example.com",
        hashed_password=hash_password("pw"),
        display_name="T",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    course = Course(user_id=user.id, name="C", description=None)
    session.add(course)
    await session.commit()
    await session.refresh(course)

    conversation = ChatConversation(course_id=course.id, title=None)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)

    # Force monotonic timestamps so the LIMIT-N-by-created_at ordering is
    # deterministic on databases with low-resolution clocks.
    for role, content in messages:
        session.add(
            ChatMessage(
                conversation_id=conversation.id,
                role=role,
                content=content,
            )
        )
        await session.commit()
        await asyncio.sleep(0.02)

    return conversation


@pytest.mark.asyncio
async def test_build_conversation_history_returns_chronological_order() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            conversation = await _create_conversation_with_messages(
                session,
                messages=[
                    ("user", "What is TCP?"),
                    ("assistant", "TCP is a transport protocol."),
                    ("user", "How does the handshake work?"),
                    ("assistant", "Three steps: SYN, SYN-ACK, ACK."),
                ],
            )

            history = await build_conversation_history(
                db=session,
                conversation_id=conversation.id,
                max_n=50,
            )

            assert [(t.role, t.content) for t in history.turns] == [
                ("student", "What is TCP?"),
                ("teacher", "TCP is a transport protocol."),
                ("student", "How does the handshake work?"),
                ("teacher", "Three steps: SYN, SYN-ACK, ACK."),
            ]
            assert history.last_student_message == "How does the handshake work?"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_build_conversation_history_caps_to_last_n_messages() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            conversation = await _create_conversation_with_messages(
                session,
                messages=[
                    ("user", "m1"),
                    ("assistant", "m2"),
                    ("user", "m3"),
                    ("assistant", "m4"),
                    ("user", "m5"),
                    ("assistant", "m6"),
                ],
            )

            history = await build_conversation_history(
                db=session,
                conversation_id=conversation.id,
                max_n=3,
            )

            assert [t.content for t in history.turns] == ["m4", "m5", "m6"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_build_conversation_history_returns_empty_for_no_messages() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            conversation = await _create_conversation_with_messages(session, messages=[])
            history = await build_conversation_history(
                db=session,
                conversation_id=conversation.id,
                max_n=10,
            )
            assert history.is_empty()
            assert history.to_prompt_string() == "(no prior messages)"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_build_conversation_history_max_n_zero_returns_empty_without_query() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            conversation = await _create_conversation_with_messages(
                session,
                messages=[("user", "real"), ("assistant", "reply")],
            )
            history = await build_conversation_history(
                db=session,
                conversation_id=conversation.id,
                max_n=0,
            )
            assert history.is_empty()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_build_conversation_history_returns_empty_for_unknown_conversation_id() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable.")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            history = await build_conversation_history(
                db=session,
                conversation_id=uuid4(),
                max_n=10,
            )
            assert history.is_empty()
    finally:
        await engine.dispose()
