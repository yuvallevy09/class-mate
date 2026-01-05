from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TranscriptSegmentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    course_id: UUID
    video_asset_id: UUID

    start_sec: float
    end_sec: float
    text: str
    language_code: str

    created_at: datetime


