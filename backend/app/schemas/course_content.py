from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CourseContentCreate(BaseModel):
    # Be tolerant to legacy fields from the existing UI (e.g. file_url/file_type).
    model_config = ConfigDict(extra="ignore")

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

    created_at: datetime


