from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MediaAssetCreate(BaseModel):
    # Reject unknown fields so clients fail fast if they send legacy/typo keys.
    model_config = ConfigDict(extra="forbid")

    file_key: str = Field(min_length=1, max_length=1024)
    original_filename: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = None

    # Optional link to a course_contents row (e.g. the "media" category item in the content library).
    content_id: UUID | None = None


class MediaAssetPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    course_id: UUID
    content_id: UUID | None

    provider: str
    status: str

    # Local upload
    source_file_key: str | None
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    audio_file_key: str | None
    thumbnail_file_key: str | None
    thumbnail_mime_type: str | None
    thumbnail_generated_at: datetime | None
    thumbnail_url: str | None = None

    # Legacy (optional now)
    video_library_id: int | None
    video_guid: str | None
    pull_zone_url: str | None

    # Transcription bookkeeping
    transcription_job_id: str | None
    transcription_error: str | None
    transcription_started_at: datetime | None
    transcription_completed_at: datetime | None
    transcript_ingested_at: datetime | None

    created_at: datetime
    updated_at: datetime


