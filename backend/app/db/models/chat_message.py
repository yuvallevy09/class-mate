from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # "user" | "assistant" (future: "system").
    role: Mapped[str] = mapped_column(String(32), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional structured citations payload for assistant messages.
    # Stored as JSONB to keep chat history stable without embedding citations into markdown.
    citations: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)

    # Optional plain-text "thinking" explanation for assistant messages (why the
    # assistant routed/searched the way it did). Persisted so it survives a
    # history reload; null for user messages and turns with nothing to show.
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


