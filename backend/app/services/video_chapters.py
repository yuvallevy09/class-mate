from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.video_chapter import VideoChapter


async def replace_with_fallback_chapter(
    *,
    db: AsyncSession,
    video_asset_id: UUID,
    language_code: str,
    end_sec: float,
) -> VideoChapter:
    """
    Replace-all chapters for a given (video_asset_id, language_code) with a single fallback
    chapter spanning the whole lecture.

    This is intentionally deterministic and safe to run multiple times.
    """
    lc = (language_code or "").strip() or "und"
    end = float(end_sec or 0.0)
    if end < 0:
        end = 0.0

    await db.execute(
        delete(VideoChapter).where(VideoChapter.video_asset_id == video_asset_id, VideoChapter.language_code == lc)
    )

    row = VideoChapter(
        video_asset_id=video_asset_id,
        language_code=lc,
        chapter_index=0,
        start_sec=0.0,
        end_sec=end,
        title="Full Lecture",
        description=None,
        artifact_version=1,
        source_hash=None,
        model_id=None,
        prompt_version=None,
    )
    db.add(row)
    await db.flush()

    # Refresh so API consumers get created/updated fields if needed.
    res = await db.execute(select(VideoChapter).where(VideoChapter.id == row.id))
    return res.scalar_one()

