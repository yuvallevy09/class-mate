from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
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

    # Provider is intentionally generic. Ingestion v2 uses local uploads (S3/MinIO).
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local", index=True)

    # Upload metadata (REQUIRED).
    source_file_key: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Transcription state (PR3 will populate these).
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="uploaded", index=True)
    # Extracted audio is uploaded back to S3 so Runpod can fetch it via a presigned URL.
    audio_file_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    transcription_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transcription_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcription_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transcription_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


