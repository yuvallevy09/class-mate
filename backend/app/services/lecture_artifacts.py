"""Service layer for generating + persisting per-lecture AI artifacts.

Structured replacement for `video_summary.generate_and_store_video_asset_summary`:
builds the full lecture transcript context, runs the `LectureArtifactGenerator`
DSPy module, and persists `ai_title` / `ai_description` / `ai_summary` (plus their
`*_generated_at` / `*_error` bookkeeping) onto `video_assets`.

Behavior:

- Needs transcript segments. Called after transcription completes (the same hook
  the legacy summary service used).
- Passes the ENTIRE transcript (high char cap) so the summary reflects the whole
  lecture rather than a truncated prefix.
- Best-effort: failures are recorded in the `*_error` columns and never raise, so
  the transcription pipeline is never broken by artifact generation.
- Idempotent: skips work when all three artifacts already exist (unless `force`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.lecture_artifact_generator import (
    LectureArtifactGenerator,
    get_lecture_artifact_generator,
)
from app.core.settings import Settings
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.video_asset import VideoAsset
from app.services.course_info import invalidate_course_info_cache


# Generous cap so full transcripts pass through. ~2M chars ≈ ~500k tokens, well
# within gemini-2.5-flash's ~1M-token input window. Kept as a safety bound rather
# than removed entirely so a pathological transcript can't blow up the prompt.
_DEFAULT_TRANSCRIPT_CHAR_BUDGET = 2_000_000

_TRANSCRIPT_MARKER = "Transcript (timestamped):"


def _fmt_timestamp(seconds: float | int | None) -> str:
    try:
        s = max(0.0, float(seconds or 0.0))
    except (TypeError, ValueError):
        s = 0.0
    mm = int(s // 60)
    ss = int(s % 60)
    return f"{mm}:{ss:02d}"


async def _build_lecture_transcript(
    *,
    db: AsyncSession,
    course_id: UUID,
    content_id: UUID | None,
    video_asset_id: UUID,
    max_chars: int = _DEFAULT_TRANSCRIPT_CHAR_BUDGET,
) -> str:
    """Render the lecture's transcript context for the generator.

    Prepends the library title/description (when available) as light context,
    then the full timestamped transcript. Returns a header-only string (without
    the `Transcript (timestamped):` marker) when there are no segments yet, so
    the caller can detect "transcript not ready".
    """
    lines: list[str] = []

    if content_id is not None:
        cres = await db.execute(
            select(CourseContent).where(
                CourseContent.id == content_id,
                CourseContent.course_id == course_id,
            )
        )
        content = cres.scalar_one_or_none()
        if content is not None:
            title = (content.title or "").strip()
            if title:
                lines.append(f"Video title: {title}")
            desc = (content.description or "").strip()
            if desc:
                lines.append(f"Video description: {desc}")

    seg_res = await db.execute(
        select(TranscriptSegment)
        .where(TranscriptSegment.video_asset_id == video_asset_id)
        .order_by(TranscriptSegment.start_sec.asc())
    )
    segs = list(seg_res.scalars().all())
    if not segs:
        return "\n".join(lines).strip()

    lines.append("")
    lines.append(_TRANSCRIPT_MARKER)

    budget = max(0, int(max_chars))
    used = sum(len(x) + 1 for x in lines)
    for seg in segs:
        text = (seg.text or "").strip()
        if not text:
            continue
        row = f"[{_fmt_timestamp(seg.start_sec)}] {text}"
        if used + len(row) + 1 > budget:
            lines.append("…")
            break
        lines.append(row)
        used += len(row) + 1

    return "\n".join(lines).strip()


def _set_error(asset: VideoAsset, message: str, *, force: bool) -> None:
    """Record a failure across all three artifact fields (respecting `force`)."""
    if force or asset.ai_title is None:
        asset.ai_title = None
        asset.ai_title_generated_at = None
        asset.ai_title_error = message
    if force or asset.ai_description is None:
        asset.ai_description = None
        asset.ai_description_generated_at = None
        asset.ai_description_error = message
    if force or asset.ai_summary is None:
        asset.ai_summary = None
        asset.ai_summary_generated_at = None
        asset.ai_summary_error = message


async def generate_and_store_lecture_artifacts(
    *,
    db: AsyncSession,
    settings: Settings,
    video_asset_id: UUID,
    force: bool = False,
    generator: LectureArtifactGenerator | None = None,
) -> VideoAsset | None:
    """Generate + persist AI title/description/summary for one video asset.

    - No-op (returns the asset) when all three already exist and `not force`.
    - Records `*_error` and returns when the transcript isn't ready.
    - Never raises: any generation failure is captured in the `*_error` columns.

    `generator` is injectable for tests; production uses the process singleton.
    """
    res = await db.execute(select(VideoAsset).where(VideoAsset.id == video_asset_id))
    asset = res.scalar_one_or_none()
    if asset is None:
        return None

    if asset.ai_title and asset.ai_summary and asset.ai_description and not force:
        return asset

    ctx = await _build_lecture_transcript(
        db=db,
        course_id=asset.course_id,
        content_id=asset.content_id,
        video_asset_id=asset.id,
    )
    if _TRANSCRIPT_MARKER not in ctx:
        _set_error(asset, "Transcript not available yet", force=force)
        await db.commit()
        return asset

    gen = generator or get_lecture_artifact_generator()
    try:
        artifacts = await gen.aforward(lecture_transcript=ctx)

        title = (artifacts.generated_title or "").strip() or None
        desc = (artifacts.generated_desc or "").strip() or None
        summary = (artifacts.generated_summary or "").strip() or None

        now = datetime.now(timezone.utc)
        if force or asset.ai_title is None:
            asset.ai_title = title
            asset.ai_title_generated_at = now if title else None
            asset.ai_title_error = None if title else "Empty title generated"
        if force or asset.ai_description is None:
            asset.ai_description = desc
            asset.ai_description_generated_at = now if desc else None
            asset.ai_description_error = None if desc else "Empty description generated"
        if force or asset.ai_summary is None:
            asset.ai_summary = summary
            asset.ai_summary_generated_at = now if summary else None
            asset.ai_summary_error = None if summary else "Empty summary generated"

        await db.commit()
        # Catalog feeding `CourseInfo` changed (title/summary used downstream).
        invalidate_course_info_cache(asset.course_id)
        return asset
    except Exception as e:
        _set_error(asset, str(e)[:2000], force=force)
        await db.commit()
        return asset
