from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat_engine import ChatEngine
from app.ai.dspy_chat import enforce_title_constraints
from app.core.settings import Settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.video_asset import VideoAsset


def _fmt_timestamp(seconds: float) -> str:
    try:
        s = max(0.0, float(seconds or 0.0))
    except Exception:
        s = 0.0
    mm = int(s // 60)
    ss = int(s % 60)
    return f"{mm}:{ss:02d}"


async def _build_video_transcript_context(
    *,
    db: AsyncSession,
    course_id: UUID,
    content_id: UUID | None,
    video_asset_id: UUID,
    max_chars: int = 200000,
) -> str:
    """
    Build a compact (but richer-than-UI) transcript context for server-side summarization.
    """
    lines: list[str] = []

    if content_id is not None:
        cres = await db.execute(
            select(CourseContent).where(CourseContent.id == content_id, CourseContent.course_id == course_id)
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
    lines.append("Transcript (timestamped):")

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


async def generate_and_store_video_asset_summary(
    *,
    db: AsyncSession,
    settings: Settings,
    video_asset_id: UUID,
    force: bool = False,
) -> VideoAsset | None:
    """
    Generate persistent AI artifacts for a video asset and store them on `video_assets`:
    - `ai_summary`: long-form lecture summary (with timestamp markers)
    - `ai_title`: short 3–5 word title

    - Safe to call multiple times: does nothing if artifacts already exist (unless force=True).
    - Best-effort: failures are recorded in `ai_*_error` and do not raise.
    """
    res = await db.execute(select(VideoAsset).where(VideoAsset.id == video_asset_id))
    asset = res.scalar_one_or_none()
    if asset is None:
        return None

    if asset.ai_summary and asset.ai_title and not force:
        return asset

    # Need transcript segments to summarize meaningfully.
    ctx = await _build_video_transcript_context(
        db=db,
        course_id=asset.course_id,
        content_id=asset.content_id,
        video_asset_id=asset.id,
        max_chars=200000,
    )
    if "Transcript (timestamped):" not in ctx:
        if force or asset.ai_summary is None:
            asset.ai_summary = None
            asset.ai_summary_generated_at = None
            asset.ai_summary_error = "Transcript not available yet"
        if force or asset.ai_title is None:
            asset.ai_title = None
            asset.ai_title_generated_at = None
            asset.ai_title_error = "Transcript not available yet"
        await db.commit()
        return asset

    # Course context for the chat engine system prompt.
    cres = await db.execute(select(Course).where(Course.id == asset.course_id))
    course = cres.scalar_one_or_none()
    course_name = str(getattr(course, "name", "") or "Course")
    course_description = str(getattr(course, "description", "") or "")

    # Prompt: ask for stable, timestamped key-point markers the frontend can linkify,
    # and a short title (used in UI, without changing the canonical course content title).
    user_message = "\n".join(
        [
            "Generate BOTH a short AI title and a comprehensive AI summary for the video lecture below.",
            "Requirements:",
            "- Output MUST be strict JSON only (no markdown, no extra text).",
            '- JSON shape: {"title": "...", "summary": "..."}',
            "- title: 3–5 words, concise, specific, not a question, no quotes.",
            "- Cover main topics, key concepts, and takeaways.",
            "- Use clear sections and bullets where helpful.",
            "- Include timestamp markers for key moments using this exact format:",
            "  [#M:SS] or a comma-separated list like [#0:04, #0:06, #0:15].",
            "  (These will be converted into clickable links in the UI.)",
            "",
            ctx,
            "",
            "Now produce the JSON.",
        ]
    ).strip()

    try:
        engine = ChatEngine(settings)
        text, _ = await engine.generate_reply(
            user_id=None,
            course_id=asset.course_id,
            course_name=course_name,
            course_description=course_description,
            history=[],
            user_message=user_message,
            rag_hits=None,
        )
        raw = (text or "").strip()

        # Be tolerant of accidental leading/trailing text: extract first JSON object.
        payload: dict | None = None
        try:
            payload = json.loads(raw) if raw else None
        except Exception:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                payload = json.loads(raw[start : end + 1])

        title = ""
        summary = ""
        if isinstance(payload, dict):
            title = str(payload.get("title") or "").strip()
            summary = str(payload.get("summary") or "").strip()
        else:
            # Legacy fallback: treat entire output as summary.
            summary = raw

        # Normalize / enforce title constraints (best-effort).
        title_norm = enforce_title_constraints(title, fallback_message=summary or "Video lecture")

        now = datetime.now(timezone.utc)
        if force or asset.ai_title is None:
            asset.ai_title = title_norm
            asset.ai_title_generated_at = now if asset.ai_title else None
            asset.ai_title_error = None if asset.ai_title else "Empty title generated"

        if force or asset.ai_summary is None:
            asset.ai_summary = summary or None
            asset.ai_summary_generated_at = now if asset.ai_summary else None
            asset.ai_summary_error = None if asset.ai_summary else "Empty summary generated"

        await db.commit()
        return asset
    except Exception as e:
        msg = str(e)[:2000]
        if force or asset.ai_summary is None:
            asset.ai_summary = None
            asset.ai_summary_generated_at = None
            asset.ai_summary_error = msg
        if force or asset.ai_title is None:
            asset.ai_title = None
            asset.ai_title_generated_at = None
            asset.ai_title_error = msg
        await db.commit()
        return asset

