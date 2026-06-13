from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.video_chapter import VideoChapter
from app.schemas.course_info import CourseInfo, Lecture
from app.schemas.retrieval import RetrievedDoc


# Field-level help text the DSPy signature surfaces to the model. Two fields
# (slug + timestamp) instead of one combined string: lets DSPy validate each
# independently and avoids forcing the model to do MM:SS -> seconds math
# (which it does unreliably).
TARGET_LECTURE_SLUG_DESC = (
    "Slug from `course_info` (e.g. 'L2') when the user pointed at a specific "
    "lecture-and-timestamp the assistant should jump to. Null when the user "
    "didn't reference a timestamp."
)
TARGET_TIMESTAMP_DESC = (
    "Timestamp inside `target_lecture_slug` in MM:SS or H:MM:SS form "
    "(e.g. '27:36' or '1:02:15'). Null when no timestamp was referenced."
)


_SLUG_RE = re.compile(r"^[Ll]\d{1,4}$")


def _parse_timestamp_part(s: str | None) -> float | None:
    """Parse a single timestamp token into seconds.

    Accepts MM:SS, H:MM:SS, or bare whole seconds (int/float). Returns None
    on malformed/out-of-range input.
    """
    text = (s or "").strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            ints = [int(p) for p in parts]
        except ValueError:
            return None
        if not (2 <= len(ints) <= 3) or any(p < 0 for p in ints):
            return None
        if len(ints) == 2:
            mm, ss = ints
            if ss >= 60:
                return None
            return float(mm * 60 + ss)
        hh, mm, ss = ints
        if mm >= 60 or ss >= 60:
            return None
        return float(hh * 3600 + mm * 60 + ss)
    try:
        v = float(text)
    except ValueError:
        return None
    return v if v >= 0 else None


def _normalize_slug(token: str | None) -> str | None:
    """Return an upper-cased slug ('L1', 'L2', ...) or None when malformed.

    Accepts mixed-case input (e.g. 'l3') and strips surrounding whitespace.
    """
    t = (token or "").strip()
    if not t:
        return None
    return t.upper() if _SLUG_RE.match(t) else None


async def _find_chapter_at(
    *,
    db: AsyncSession,
    video_asset_id: UUID,
    ts: float,
) -> VideoChapter | None:
    """Return the chapter whose [start_sec, end_sec) contains `ts`, or None.

    Queried on demand for a single lecture (this path is rare) rather than
    preloaded into `CourseInfo` on every chat turn.

    Half-open on the end so adjacent chapters (A ends at 600.0, B starts at
    600.0) attribute `ts=600.0` to B, matching the natural reading of "this is
    where the next chapter begins". Matches `_load_segments_in_range`, which is
    also half-open. When chapters overlap (shouldn't happen, but be defensive),
    prefer the lowest `chapter_index` so behavior is deterministic.
    """
    stmt = (
        select(VideoChapter)
        .where(
            VideoChapter.video_asset_id == video_asset_id,
            VideoChapter.start_sec <= ts,
            VideoChapter.end_sec > ts,
        )
        .order_by(VideoChapter.chapter_index.asc())
        .limit(1)
    )
    res = await db.execute(stmt)
    return res.scalars().first()


async def _load_segments_in_range(
    *,
    db: AsyncSession,
    video_asset_id: UUID,
    start_sec: float,
    end_sec: float,
) -> list[TranscriptSegment]:
    """Load all transcript segments overlapping [start_sec, end_sec], ordered by start."""
    stmt = (
        select(TranscriptSegment)
        .where(
            TranscriptSegment.video_asset_id == video_asset_id,
            TranscriptSegment.end_sec > start_sec,
            TranscriptSegment.start_sec < end_sec,
        )
        .order_by(TranscriptSegment.start_sec.asc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


def _fmt_timestamp(seconds: float | int | None) -> str:
    try:
        s = max(0.0, float(seconds or 0.0))
    except (TypeError, ValueError):
        s = 0.0
    mm = int(s // 60)
    ss = int(s % 60)
    return f"{mm}:{ss:02d}"


def _render_segments(segments: list[TranscriptSegment]) -> str:
    lines: list[str] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        lines.append(f"[{_fmt_timestamp(seg.start_sec)}] {text}")
    return "\n".join(lines).strip()


async def retrieve_explicitly(
    *,
    db: AsyncSession,
    course_info: CourseInfo,
    lecture_slug: str | None,
    timestamp: str | None,
    fallback_window_sec: float = 60.0,
) -> list[RetrievedDoc]:
    """Explicit, deterministic retrieval driven by a (slug, timestamp) pair.

    Strategy:
    1. Validate both inputs. Bail (empty list) if either is missing or malformed.
    2. Resolve the slug against `course_info`. Bail if unknown or transcript not ready.
    3. If a VideoChapter contains the timestamp, use that chapter's [start, end]
       as the retrieval window (semantic boundary). Otherwise fall back to a
       ±`fallback_window_sec/2` window around the timestamp.
    4. Load all overlapping `TranscriptSegment` rows and render them with inline
       `[M:SS]` markers, wrapped in a single `RetrievedDoc`.

    Returns an empty list whenever retrieval can't produce useful context — the
    caller is expected to fall through to the lecture/hybrid retrieval path.
    """
    slug = _normalize_slug(lecture_slug)
    if slug is None:
        return []

    ts_sec = _parse_timestamp_part(timestamp)
    if ts_sec is None:
        return []

    lecture: Lecture | None = course_info.lecture_by_slug(slug)
    if lecture is None or not lecture.transcript_ready:
        return []

    chapter = await _find_chapter_at(db=db, video_asset_id=lecture.id, ts=ts_sec)
    if chapter is not None:
        window_start = float(chapter.start_sec)
        window_end = float(chapter.end_sec)
    else:
        half = max(0.0, float(fallback_window_sec)) / 2.0
        window_start = max(0.0, ts_sec - half)
        window_end = ts_sec + half

    segments = await _load_segments_in_range(
        db=db,
        video_asset_id=lecture.id,
        start_sec=window_start,
        end_sec=window_end,
    )
    body = _render_segments(segments)
    if not body:
        return []

    actual_start = float(segments[0].start_sec) if chapter is None else window_start
    actual_end = float(segments[-1].end_sec) if chapter is None else window_end

    return [
        RetrievedDoc(
            text=body,
            lecture_id=lecture.id,
            lecture_slug=lecture.slug,
            lecture_title=lecture.title,
            content_id=lecture.content_id,
            chapter_id=chapter.id if chapter is not None else None,
            chapter_title=chapter.title if chapter is not None else None,
            start_sec=actual_start,
            end_sec=actual_end,
        )
    ]
