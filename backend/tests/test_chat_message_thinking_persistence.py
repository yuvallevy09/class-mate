"""DB round-trip test for persisted assistant `thinking`.

Verifies the migration column + ORM field + `ChatMessagePublic` serialization
all line up: an assistant message stored with `thinking` reads back with it, and
the public schema (what the history endpoint returns) exposes it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.chat_conversation import ChatConversation
from app.db.models.chat_message import ChatMessage
from app.db.models.course import Course
from app.db.models.user import User
from app.schemas.chat_persistence import ChatMessagePublic


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


@pytest.mark.asyncio
async def test_assistant_thinking_round_trips_through_db_and_schema() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            user = User(email=f"u-{uuid4()}@e.com", hashed_password=hash_password("pw"), display_name="T")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            course = Course(user_id=user.id, name="Course", description=None)
            session.add(course)
            await session.commit()
            await session.refresh(course)

            conversation = ChatConversation(course_id=course.id, title=None)
            session.add(conversation)
            await session.flush()

            session.add_all(
                [
                    ChatMessage(conversation_id=conversation.id, role="user", content="hi"),
                    ChatMessage(
                        conversation_id=conversation.id,
                        role="assistant",
                        content="Answer [1].",
                        thinking="Searched lectures L2, L3 — found 2 relevant passages.",
                    ),
                ]
            )
            await session.commit()

            rows = (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.conversation_id == conversation.id)
                    .order_by(ChatMessage.created_at.asc())
                )
            ).scalars().all()

            public = [ChatMessagePublic.model_validate(m) for m in rows]
            user_msg, assistant_msg = public

            # User messages carry no thinking; assistant message persists + exposes it.
            assert user_msg.thinking is None
            assert assistant_msg.thinking == "Searched lectures L2, L3 — found 2 relevant passages."
    finally:
        await engine.dispose()
