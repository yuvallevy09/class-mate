from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat_message import ChatMessage
from app.schemas.conversation_history import ConversationHistory


async def build_conversation_history(
    *,
    db: AsyncSession,
    conversation_id: UUID,
    max_n: int,
) -> ConversationHistory:
    """Load the last `max_n` `ChatMessage` rows for a conversation and return a
    `ConversationHistory` with chronological order (oldest -> newest).

    Mirrors the load pattern in `app/api/v1/chat.py:course_chat` so the new
    pipeline and the legacy endpoint converge on the same definition of
    "conversation history".

    Intended to be called BEFORE persisting the current user message, so the
    returned history does not include it. The TeachingAssistant receives the
    current user message separately via `user_query`.
    """
    if max_n <= 0:
        return ConversationHistory(turns=[])

    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(max_n)
    )
    res = await db.execute(stmt)
    recent_desc = list(res.scalars().all())
    recent_asc = list(reversed(recent_desc))

    return ConversationHistory.from_chat_messages(recent_asc, max_n=max_n)
