from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.chat import ChatCitation


class ChatConversationPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    course_id: UUID
    title: str | None
    created_at: datetime
    last_message_at: datetime


class ChatMessagePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    role: str
    content: str
    citations: list[ChatCitation] | None = None
    thinking: str | None = None
    created_at: datetime


