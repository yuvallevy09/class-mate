from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ChatCitation(BaseModel):
    """
    Future-proof citation payload for RAG. Keep fields optional for now so we can
    start returning citations later without changing the contract.
    """

    content_id: UUID | None = None
    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    # Allow extra provider-specific fields later without breaking.
    extra: dict[str, Any] | None = None


class CourseChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=128, validation_alias="conversationId")

    # --- Video-player viewing context (optional) ---
    # Presence of `watching_video_asset_id` is itself the "video mode" switch:
    # when set, the chat scopes retrieval toward that lecture and anchors the
    # answer to what the student is currently watching. Both fields degrade
    # gracefully — an unknown/not-ready asset is ignored and the turn behaves
    # like ordinary course chat (resolved in the endpoint, not here).
    watching_video_asset_id: UUID | None = Field(
        default=None, validation_alias="watchingVideoAssetId"
    )
    watching_timestamp_sec: float | None = Field(
        default=None, ge=0, validation_alias="watchingTimestampSec"
    )

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Message is required")
        return v


class CourseChatResponse(BaseModel):
    text: str
    citations: list[ChatCitation] = Field(default_factory=list)
    conversation_id: UUID | None = Field(default=None, serialization_alias="conversationId")
    # Plain-text "thinking" explanation for the live turn (why the assistant
    # routed/searched the way it did). Per-turn only — not persisted with the
    # message, so it doesn't survive a history reload. Null when there's nothing
    # substantive to show; the UI hides the reasoning panel in that case.
    thinking: str | None = None
