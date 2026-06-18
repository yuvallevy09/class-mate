from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.dspy_chapters import generate_chapters_dspy
from app.ai.llm import litellm_model_id
from app.core.settings import Settings, get_settings
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.video_chapter import VideoChapter


@dataclass(frozen=True)
class TranscriptBlock:
    block_index: int
    start_sec: float
    end_sec: float
    text: str


def _build_blocks_from_segments(
    *,
    segments: list[TranscriptSegment],
    target_block_seconds: float = 20.0,
    max_block_seconds: float = 30.0,
) -> list[TranscriptBlock]:
    """
    Deterministically merge raw transcript segments into coarse blocks (10–30s-ish).
    These blocks are the input for semantic chapterization to reduce cost/noise.
    """
    target = max(5.0, float(target_block_seconds))
    max_s = max(target, float(max_block_seconds))

    out: list[TranscriptBlock] = []
    if not segments:
        return out

    buf: list[str] = []
    block_start = float(segments[0].start_sec)
    block_end = float(segments[0].end_sec)

    def flush():
        nonlocal buf, block_start, block_end
        txt = " ".join([t.strip() for t in buf if t and t.strip()]).strip()
        if txt:
            out.append(
                TranscriptBlock(
                    block_index=len(out),
                    start_sec=float(block_start),
                    end_sec=float(block_end),
                    text=txt,
                )
            )
        buf = []

    for seg in segments:
        t = (seg.text or "").strip()
        if not t:
            continue
        if not buf:
            block_start = float(seg.start_sec)
            block_end = float(seg.end_sec)
        # Would adding this segment exceed the max block duration?
        next_end = float(seg.end_sec)
        dur_if_added = max(0.0, next_end - float(block_start))
        dur_now = max(0.0, float(block_end) - float(block_start))
        if buf and (dur_if_added > max_s) and (dur_now >= 10.0 or dur_now >= target):
            flush()
            block_start = float(seg.start_sec)
            block_end = float(seg.end_sec)
        block_end = float(seg.end_sec)
        buf.append(t)
        dur_now = max(0.0, float(block_end) - float(block_start))
        if dur_now >= target and dur_now >= 10.0:
            flush()

    if buf:
        flush()
    return out


def _hash_blocks(blocks: list[TranscriptBlock]) -> str:
    h = hashlib.sha256()
    for b in blocks:
        h.update(f"{b.block_index}|{b.start_sec:.3f}|{b.end_sec:.3f}|{b.text}\n".encode("utf-8"))
    return h.hexdigest()


def _normalize_chapters_payload(payload: dict) -> list[dict]:
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        return []
    out: list[dict] = []
    for c in chapters:
        if not isinstance(c, dict):
            continue
        out.append(c)
    return out


async def replace_with_semantic_chapters(
    *,
    db: AsyncSession,
    video_asset_id: UUID,
    language_code: str,
    settings: Settings,
    blocks: list[TranscriptBlock],
) -> list[VideoChapter]:
    """
    Replace-all chapters for (video_asset_id, language_code) using DSPy chapterizer output.
    Raises on validation failures; caller should fall back to `replace_with_fallback_chapter`.
    """
    lc = (language_code or "").strip() or "und"
    if not blocks:
        raise ValueError("No transcript blocks available for chapterization")

    payload = generate_chapters_dspy(
        settings=settings,
        blocks=[
            {"block_index": b.block_index, "start_sec": b.start_sec, "end_sec": b.end_sec, "text": b.text}
            for b in blocks
        ],
    )
    chapters_raw = _normalize_chapters_payload(payload if isinstance(payload, dict) else {})
    if not chapters_raw:
        raise ValueError("Chapterizer returned no chapters")

    max_block = len(blocks) - 1

    # Validate and coerce.
    chapters_norm: list[dict] = []
    last_end = -1
    for c in chapters_raw:
        try:
            sb = int(c.get("start_block"))
            eb = int(c.get("end_block"))
        except Exception:
            continue
        if sb < 0 or eb < 0 or sb > eb or sb > max_block or eb > max_block:
            continue
        if sb <= last_end:
            # Must be strictly monotonic and non-overlapping.
            continue
        title = str(c.get("title") or "").strip()
        desc = str(c.get("description") or "").strip() or None
        if not title:
            continue
        chapters_norm.append({"start_block": sb, "end_block": eb, "title": title[:255], "description": desc})
        last_end = eb

    if not chapters_norm:
        raise ValueError("No valid chapters after validation")

    # Replace-all existing chapters.
    await db.execute(
        delete(VideoChapter).where(VideoChapter.video_asset_id == video_asset_id, VideoChapter.language_code == lc)
    )

    src_hash = _hash_blocks(blocks)
    model_id = litellm_model_id(settings)
    prompt_version = "chapters_v1"

    rows: list[VideoChapter] = []
    for idx, c in enumerate(chapters_norm):
        sb = int(c["start_block"])
        eb = int(c["end_block"])
        start_sec = float(blocks[sb].start_sec)
        end_sec = float(blocks[eb].end_sec)
        rows.append(
            VideoChapter(
                video_asset_id=video_asset_id,
                language_code=lc,
                chapter_index=int(idx),
                start_sec=float(start_sec),
                end_sec=float(end_sec),
                title=str(c["title"]),
                description=c.get("description"),
                artifact_version=1,
                source_hash=src_hash,
                model_id=model_id,
                prompt_version=prompt_version,
            )
        )

    db.add_all(rows)
    await db.flush()
    return rows


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


async def chapterize_or_fallback(
    *,
    db: AsyncSession,
    video_asset_id: UUID,
    language_code: str,
    end_sec: float,
) -> list[VideoChapter]:
    """
    Best-effort orchestration:
    - build coarse blocks from transcript_segments
    - try semantic chapterization via DSPy
    - fall back to a single "Full Lecture" chapter on any failure
    """
    lc = (language_code or "").strip() or "und"
    settings = get_settings()

    # Feature-flagged off by default: chapters aren't surfaced in the player UI yet and
    # only lightly affect retrieval, so we skip the LLM call and write the single
    # "Full Lecture" fallback. Flip CHAPTERS_ENABLED=true to generate real chapters.
    if getattr(settings, "chapters_enabled", False):
        seg_res = await db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.video_asset_id == video_asset_id, TranscriptSegment.language_code == lc)
            .order_by(TranscriptSegment.start_sec.asc())
        )
        segs = list(seg_res.scalars().all())
        blocks = _build_blocks_from_segments(segments=segs, target_block_seconds=20.0, max_block_seconds=30.0)
        if blocks:
            try:
                return await replace_with_semantic_chapters(
                    db=db, video_asset_id=video_asset_id, language_code=lc, settings=settings, blocks=blocks
                )
            except Exception:
                pass

    # Fallback (also the default path when CHAPTERS_ENABLED is false — no model call).
    fb = await replace_with_fallback_chapter(db=db, video_asset_id=video_asset_id, language_code=lc, end_sec=end_sec)
    return [fb]

