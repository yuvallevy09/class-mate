from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VideoAssetCreate(BaseModel):
    # Reject unknown fields so clients fail fast if they send legacy/typo keys.
    model_config = ConfigDict(extra="forbid")

    source_file_key: str = Field(min_length=1, max_length=1024)
    original_filename: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = None

    # Optional link to a course_contents row (e.g. a "video" content item in the content library UI).
    content_id: UUID | None = None


class VideoAssetPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    course_id: UUID
    content_id: UUID | None

    provider: str
    status: str

    source_file_key: str
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    audio_file_key: str | None

    transcription_job_id: str | None
    transcription_error: str | None
    transcription_started_at: datetime | None
    transcription_completed_at: datetime | None
    transcript_ingested_at: datetime | None

    created_at: datetime
    updated_at: datetime


