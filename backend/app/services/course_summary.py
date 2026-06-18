"""Service layer for generating + persisting the course-level AI summary.

Builds a context from the course's per-lecture descriptions (chronological;
falling back to the full lecture summary when a lecture has no description),
runs the `CourseSummaryGenerator` DSPy module, and persists `ai_summary` (plus
`ai_summary_generated_at` / `ai_summary_error`) onto `courses`.

Behavior:

- Called after a lecture finishes transcribing (sibling of the per-lecture
  artifact hook in the transcription pipeline), so the summary is regenerated
  on every new lecture.
- Replace-on-success: the previous summary stays in place until a new one is
  fully generated. Failures only record `ai_summary_error`.
- Best-effort: never raises, so the transcription pipeline can't be broken by
  course summary generation.
- No locking: two lectures finishing simultaneously means last-writer-wins,
  and both runs see all done lectures, so the results are equivalent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.course_summary_generator import (
    CourseSummaryGenerator,
    get_course_summary_generator,
)
from app.core.settings import Settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.video_asset import VideoAsset
from app.services.course_info import invalidate_course_info_cache


# Statuses with a usable transcript (and therefore lecture artifacts).
_DONE_STATUSES = ("done", "done_no_embeddings", "done_no_index")

# Lecture descriptions are short (1–3 sentences); this bound keeps a
# pathological course (or a fallback to full summaries) from blowing up the prompt.
_DEFAULT_CONTEXT_CHAR_BUDGET = 200_000


def _build_course_context(
    *,
    course_name: str,
    course_description: str | None,
    lectures: list[tuple[str, str]],
    max_chars: int = _DEFAULT_CONTEXT_CHAR_BUDGET,
) -> str:
    """Render the generator input from (title, summary) pairs, oldest first.

    Pure function so ordering/truncation are unit-testable. Lectures that don't
    fit the budget are dropped from the end with an ellipsis marker.
    """
    lines: list[str] = [f"Course: {course_name.strip()}"]
    desc = (course_description or "").strip()
    if desc:
        lines.append(f"Course description: {desc}")

    budget = max(0, int(max_chars))
    used = sum(len(x) + 1 for x in lines)
    for idx, (title, summary) in enumerate(lectures, start=1):
        block = f"\nLecture {idx} — {title.strip()}:\n{summary.strip()}"
        if used + len(block) + 1 > budget:
            lines.append("…")
            break
        lines.append(block)
        used += len(block) + 1

    return "\n".join(lines).strip()


async def generate_and_store_course_summary(
    *,
    db: AsyncSession,
    settings: Settings,
    course_id: UUID,
    generator: CourseSummaryGenerator | None = None,
) -> Course | None:
    """Generate + persist the course-level AI summary, replacing the old one.

    - Always regenerates when called (a call means the lecture set changed).
    - No-op when the course has no usable lecture summaries yet.
    - Never raises: failures land in `ai_summary_error` and the previous
      summary (if any) is left untouched.

    `generator` is injectable for tests; production uses the process singleton.
    """
    res = await db.execute(select(Course).where(Course.id == course_id))
    course = res.scalar_one_or_none()
    if course is None:
        return None

    rows = await db.execute(
        select(VideoAsset, CourseContent)
        .join(CourseContent, CourseContent.id == VideoAsset.content_id)
        .where(
            VideoAsset.course_id == course_id,
            VideoAsset.status.in_(_DONE_STATUSES),
        )
        .order_by(CourseContent.created_at.asc(), VideoAsset.created_at.asc())
    )

    lectures: list[tuple[str, str]] = []
    for asset, content in rows.all():
        # Prefer the short per-lecture description over the full summary: the
        # course recap only needs each lecture's gist, and descriptions keep the
        # combined context an order of magnitude smaller. Fall back to the full
        # summary when a lecture has no description yet.
        body = (asset.ai_description or "").strip() or (asset.ai_summary or "").strip()
        if not body:
            continue
        title = (
            (content.title or "").strip()
            or (asset.ai_title or "").strip()
            or f"Lecture {len(lectures) + 1}"
        )
        lectures.append((title, body))

    if not lectures:
        # Nothing usable yet; keep whatever summary exists rather than clearing it.
        return course

    ctx = _build_course_context(
        course_name=course.name,
        course_description=course.description,
        lectures=lectures,
    )

    gen = generator or get_course_summary_generator()
    try:
        result = await gen.aforward(course_context=ctx)
        summary = (result.generated_summary or "").strip() or None
        if summary is None:
            course.ai_summary_error = "Empty course summary generated"
        else:
            course.ai_summary = summary
            course.ai_summary_generated_at = datetime.now(timezone.utc)
            course.ai_summary_error = None
        await db.commit()
        invalidate_course_info_cache(course.id)
        return course
    except Exception as e:
        # Keep the previous summary visible; only record why the refresh failed.
        course.ai_summary_error = str(e)[:2000]
        await db.commit()
        return course
