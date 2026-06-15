from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    # Backs the video-chat widget's "conversations for this lecture" lookup:
    # filter by (course_id, video_asset_id), newest first.
    __table_args__ = (
        Index(
            "ix_chat_conversations_course_video_last_msg",
            "course_id",
            "video_asset_id",
            "last_message_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The lecture this conversation is anchored to, when it was started from the
    # video player. NULL = course-level chat (the default). Set so per-video
    # conversation history can be listed/switched in the video-chat widget; the
    # composite index above backs that filtered, time-sorted lookup.
    video_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("video_assets.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Optional human-friendly label (future: auto-title from first user message).
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


