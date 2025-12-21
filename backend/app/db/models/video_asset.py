from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VideoAsset(Base):
    __tablename__ = "video_assets"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Optional link back to a course_contents row (e.g. a "video" content item in the library UI).
    content_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("course_contents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="bunny", index=True)

    # Bunny-specific identifiers.
    video_library_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_guid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pull_zone_url: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Status tracking (store both a stable string and the last numeric webhook status code).
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="queued", index=True)
    last_webhook_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_webhook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    captions_language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    captions_vtt_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    captions_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transcript_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


