from __future__ import annotations

from uuid import UUID

from cachetools import TTLCache
from cachetools.keys import hashkey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.video_asset import VideoAsset
from app.db.models.video_chapter import VideoChapter
from app.schemas.course_info import CourseInfo, Lecture, LectureChapter


# --- TTL cache ---
#
# `build_course_info` is hit on every chat turn (once `TeachingAssistant` is
# wired into the endpoint). The output only changes when lectures/contents/
# chapters/AI titles/summaries change — i.e. on uploads, transcript ingestion,
# and explicit edits. Within a typical chat session (a few minutes) the catalog
# is effectively immutable.
#
# A 5-minute TTL gives us:
#   - high hit rate within a chat session,
#   - cheap-enough first call (one DB join + one chapter query),
#   - bounded staleness when new lectures land mid-conversation (worst case 5min).
#
# Mutation sites should call `invalidate_course_info_cache(course_id)` to drop
# stale entries proactively. The TTL is the safety net for cases we miss.
#
# Thread/process notes:
#   - asyncio single-threaded workers are safe under the GIL: even a racy
#     get-then-set yields "last write wins" with identical values.
#   - Multi-process workers (uvicorn --workers N) each get their own cache.
#     Eventually consistent across workers within the TTL window — acceptable.
DEFAULT_COURSE_INFO_CACHE_TTL_SEC = 300  # 5 minutes
DEFAULT_COURSE_INFO_CACHE_MAXSIZE = 512
_COURSE_INFO_CACHE: TTLCache = TTLCache(
    maxsize=DEFAULT_COURSE_INFO_CACHE_MAXSIZE,
    ttl=DEFAULT_COURSE_INFO_CACHE_TTL_SEC,
)


def _cache_key(course_id: UUID, *, include_chapters: bool) -> tuple:
    """Stable cache key. `include_chapters` is part of the key because it
    materially changes the returned object (no chapters vs full chapters)."""
    return hashkey(str(course_id), bool(include_chapters))


def invalidate_course_info_cache(course_id: UUID | str) -> None:
    """Drop both cached variants (`include_chapters` True/False) for a course.

    Call this from any code path that mutates data feeding into `CourseInfo`:
    creating/deleting `CourseContent` or `VideoAsset`, ingesting transcripts
    (flipping `transcript_ready`), creating `VideoChapter` rows, updating
    `ai_title` / `ai_summary`, renaming the course, etc.

    Safe to call with a UUID or its string form. No-op when the key is absent.
    """
    cid = course_id if isinstance(course_id, UUID) else UUID(str(course_id))
    for include_chapters in (True, False):
        _COURSE_INFO_CACHE.pop(_cache_key(cid, include_chapters=include_chapters), None)


def clear_course_info_cache() -> None:
    """Drop every cached entry. Intended for tests; harmless in production."""
    _COURSE_INFO_CACHE.clear()


async def build_course_info_cached(
    *,
    db: AsyncSession,
    course: Course,
    include_chapters: bool = True,
) -> CourseInfo:
    """Cached variant of `build_course_info` with a TTL.

    See `build_course_info` for behavior. Cache misses delegate to the
    uncached builder and store the result; cache hits return immediately
    without touching the DB.

    When in doubt about freshness, call `invalidate_course_info_cache(course.id)`
    after mutating any of the underlying tables.
    """
    key = _cache_key(course.id, include_chapters=include_chapters)
    cached = _COURSE_INFO_CACHE.get(key)
    if cached is not None:
        return cached
    info = await build_course_info(
        db=db, course=course, include_chapters=include_chapters
    )
    _COURSE_INFO_CACHE[key] = info
    return info


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
