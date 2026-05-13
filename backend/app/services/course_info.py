from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.video_asset import VideoAsset
from app.db.models.video_chapter import VideoChapter
from app.schemas.course_info import CourseInfo, Lecture, LectureChapter


def _pick_lecture_title(*, asset: VideoAsset, content: CourseContent | None) -> str:
    """Pick the best human-readable title for a lecture.

    Order of preference matches what we already do in the chat citation layer:
    AI-generated title -> library content title -> uploaded filename.
    """
    candidates: list[str | None] = [
        getattr(asset, "ai_title", None),
        getattr(content, "title", None) if content is not None else None,
        getattr(asset, "original_filename", None),
        getattr(content, "original_filename", None) if content is not None else None,
    ]
    for c in candidates:
        if not c:
            continue
        s = str(c).strip()
        if s:
            return s
    return "Untitled lecture"


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


async def _load_chapters_by_asset(
    *,
    db: AsyncSession,
    asset_ids: list[UUID],
) -> dict[UUID, list[LectureChapter]]:
    """Batch-load chapters for many video assets and group by `video_asset_id`."""
    if not asset_ids:
        return {}

    res = await db.execute(
        select(VideoChapter)
        .where(VideoChapter.video_asset_id.in_(asset_ids))
        .order_by(
            VideoChapter.video_asset_id.asc(),
            VideoChapter.chapter_index.asc(),
        )
    )
    by_asset: dict[UUID, list[LectureChapter]] = {}
    for row in res.scalars().all():
        by_asset.setdefault(row.video_asset_id, []).append(
            LectureChapter.model_validate(row)
        )
    return by_asset


async def build_course_info(
    *,
    db: AsyncSession,
    course: Course,
    include_chapters: bool = True,
) -> CourseInfo:
    """Build a `CourseInfo` snapshot for a course.

    - Loads all `VideoAsset` rows for the course joined with their `CourseContent` rows.
    - Orders lectures chronologically (oldest first) so the per-turn `L1`, `L2`, ...
      slugs are stable across turns of the same conversation.
    - When `include_chapters=True`, fetches all `VideoChapter` rows in one batched
      query and derives `duration_sec` from the last chapter's `end_sec`.
    - Best-effort: missing data degrades to `None` rather than raising.

    Note: this loader does not currently filter to a token budget. Truncation for
    LLM input is the responsibility of `CourseInfo.to_prompt_string(max_chars=...)`.
    """
    res = await db.execute(
        select(VideoAsset, CourseContent)
        .join(CourseContent, CourseContent.id == VideoAsset.content_id)
        .where(VideoAsset.course_id == course.id)
        .order_by(
            CourseContent.created_at.asc(),
            VideoAsset.created_at.asc(),
        )
    )
    rows: list[tuple[VideoAsset, CourseContent]] = list(res.all())

    asset_ids: list[UUID] = [asset.id for (asset, _content) in rows]

    chapters_by_asset: dict[UUID, list[LectureChapter]] = {}
    if include_chapters and asset_ids:
        chapters_by_asset = await _load_chapters_by_asset(db=db, asset_ids=asset_ids)

    lectures: list[Lecture] = []
    for idx, (asset, content) in enumerate(rows, start=1):
        chapters = chapters_by_asset.get(asset.id, [])
        duration_sec: float | None = (
            max((c.end_sec for c in chapters)) if chapters else None
        )

        lectures.append(
            Lecture(
                id=asset.id,
                content_id=content.id if content is not None else None,
                slug=f"L{idx}",
                title=_pick_lecture_title(asset=asset, content=content),
                description=_clean_optional(
                    content.description if content is not None else None
                ),
                summary=_clean_optional(asset.ai_summary),
                duration_sec=duration_sec,
                transcript_ready=asset.transcript_ingested_at is not None,
                chapters=chapters,
            )
        )

    return CourseInfo(
        id=course.id,
        name=course.name,
        description=_clean_optional(course.description),
        summary=None,  # TODO: populate once a course-level summary is available.
        lectures=lectures,
    )
