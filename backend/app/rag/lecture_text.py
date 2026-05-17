from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.transcript_segment import TranscriptSegment
from app.schemas.course_info import CourseInfo
from app.schemas.retrieval import RetrievedDoc


# Default character budget for the short-lecture bypass.
# ~4 chars/token in English; 24,000 chars ≈ 6,000 tokens. This leaves headroom
# for the system prompt, course_info, conversation history, retrieved-docs
# wrapper, and the model's own answer within a typical 32k-128k context window.
DEFAULT_FULL_TRANSCRIPT_CHAR_BUDGET = 24_000


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


async def get_lecture_text(
    *,
    db: AsyncSession,
    course_info: CourseInfo,
    lecture_slugs: list[str] | None,
    char_budget: int = DEFAULT_FULL_TRANSCRIPT_CHAR_BUDGET,
) -> tuple[list[RetrievedDoc], bool]:
    """Fetch the full transcript for each requested lecture.

    Returns `(docs, is_long)` where:

    - `docs` is one `RetrievedDoc` per lecture, with the full transcript as the
      body (timestamped with `[M:SS]` markers) and lecture metadata attached
      for downstream citation building.
    - `is_long` is `True` when the combined character count exceeds
      `char_budget`. When `True`, the caller should drop `docs` and fall
      through to a chunked retrieval strategy instead of pasting full
      transcripts into the prompt.

    Filters quietly applied:
    - Unknown slugs are skipped (no raise — we trust `CourseInfo` resolution).
    - Lectures with `transcript_ready=False` are skipped (no transcript to fetch).
    - Lectures with no transcript segments are skipped (data hasn't ingested yet).

    When nothing resolves to a usable lecture, returns `([], False)`. The caller
    can distinguish "no resolvable lectures" from "lectures present but too long"
    via the boolean.
    """
    if not lecture_slugs:
        return [], False

    lectures = course_info.lectures_by_slugs(lecture_slugs)
    ready = [lec for lec in lectures if lec.transcript_ready]
    if not ready:
        return [], False

    asset_ids: list[UUID] = [lec.id for lec in ready]
    stmt = (
        select(TranscriptSegment)
        .where(TranscriptSegment.video_asset_id.in_(asset_ids))
        .order_by(
            TranscriptSegment.video_asset_id.asc(),
            TranscriptSegment.start_sec.asc(),
        )
    )
    res = await db.execute(stmt)
    segs_by_asset: dict[UUID, list[TranscriptSegment]] = {}
    for seg in res.scalars().all():
        segs_by_asset.setdefault(seg.video_asset_id, []).append(seg)

    docs: list[RetrievedDoc] = []
    total_chars = 0
    for lec in ready:
        segments = segs_by_asset.get(lec.id, [])
        if not segments:
            continue
        body = _render_segments(segments)
        if not body:
            continue
        total_chars += len(body)
        docs.append(
            RetrievedDoc(
                text=body,
                lecture_id=lec.id,
                lecture_slug=lec.slug,
                lecture_title=lec.title,
                content_id=lec.content_id,
                chapter_id=None,
                chapter_title=None,
                start_sec=float(segments[0].start_sec),
                end_sec=float(segments[-1].end_sec),
            )
        )

    if not docs:
        return [], False

    is_long = total_chars > max(0, int(char_budget))
    return docs, is_long
