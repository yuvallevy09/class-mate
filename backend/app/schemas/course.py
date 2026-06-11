from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Course name is required")
        return v


class CoursePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    # Course-level AI summary, regenerated after each new lecture transcription.
    ai_summary: str | None = None
    ai_summary_generated_at: datetime | None = None
    created_at: datetime


