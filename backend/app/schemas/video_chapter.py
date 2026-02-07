from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class VideoChapterPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    video_asset_id: UUID
    language_code: str

    chapter_index: int
    start_sec: float
    end_sec: float

    title: str
    description: str | None = None

    artifact_version: int
    source_hash: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None

    created_at: datetime
    updated_at: datetime

