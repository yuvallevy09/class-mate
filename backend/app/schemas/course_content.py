from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


ALLOWED_COURSE_CONTENT_CATEGORIES: set[str] = {
    "overview",
    "media",
    "notes",
    "assignments",
    "exams",
    "additional_resources",
}


class CourseContentCreate(BaseModel):
    # Reject unknown fields so clients fail fast if they send legacy/typo keys.
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)

    file_key: str | None = None
    original_filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None

    @field_validator("category", "title")
    @classmethod
    def _strip_required_strings(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Value is required")
        return v

    @field_validator("category")
    @classmethod
    def _validate_category(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in ALLOWED_COURSE_CONTENT_CATEGORIES:
            raise ValueError(f"Invalid category: {v}")
        return v


class CourseContentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    course_id: UUID
    category: str
    title: str
    description: str | None

    file_key: str | None
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None

    ingestion_status: str
    ingestion_warning: str | None = None
    ingestion_error: str | None = None
    ingestion_started_at: datetime | None = None
    ingestion_completed_at: datetime | None = None

    created_at: datetime


