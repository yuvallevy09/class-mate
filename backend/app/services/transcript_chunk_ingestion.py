from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.db.models.content_chunk import ContentChunk
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.video_asset import VideoAsset
from app.db.models.video_chapter import VideoChapter
from app.rag.embeddings import get_embeddings
from app.rag.embedding_config import EMBEDDING_DIMS
from app.services.video_chapters import replace_with_fallback_chapter


@dataclass(frozen=True)
class TranscriptPiece:
    start_sec: float
    end_sec: float
    text: str


@dataclass(frozen=True)
class TranscriptChunkIngestResult:
    chunks_written: int
    embeddings_written: bool


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.9g}" for x in vec) + "]"


def _chunk_transcript(
    *,
    pieces: list[TranscriptPiece],
    chunk_size: int,
    overlap_chars: int,
) -> list[tuple[float, float, str]]:
    """
    Chunk transcript pieces into coherent-ish windows by character budget, preserving time ranges.

    - Greedy append until chunk_size exceeded
    - Add simple character overlap by carrying tail text
    """
    out: list[tuple[float, float, str]] = []
    if not pieces:
        return out

    chunk_size = max(200, int(chunk_size))
    overlap_chars = max(0, int(overlap_chars))

    buf: list[str] = []
    start = float(pieces[0].start_sec)
    end = float(pieces[0].end_sec)
    buf_len = 0

    def flush():
        nonlocal buf, start, end, buf_len
        txt = " ".join([t.strip() for t in buf if t.strip()]).strip()
        if txt:
            out.append((start, end, txt))
        # Prepare overlap: keep last overlap_chars of text as seed
        if overlap_chars > 0 and txt:
            tail = txt[-overlap_chars:]
            buf = [tail]
            buf_len = len(tail)
        else:
            buf = []
            buf_len = 0

    for p in pieces:
        t = (p.text or "").strip()
        if not t:
            continue
        # If this piece alone is huge, flush current buffer then store as its own chunk(s).
        if len(t) > chunk_size * 2:
            if buf:
                flush()
            out.append((float(p.start_sec), float(p.end_sec), t[: max(chunk_size, 500)]))
            continue

        # If adding would exceed budget, flush first.
        if buf and (buf_len + 1 + len(t) > chunk_size):
            flush()
            start = float(p.start_sec)
            end = float(p.end_sec)

        if not buf:
            start = float(p.start_sec)
        end = float(p.end_sec)
        buf.append(t)
        buf_len += len(t) + 1

    if buf:
        flush()
    return out


_SENTENCE_END_RE = re.compile(r"[.!?]+")


def _estimate_tokens(text: str) -> int:
    # Approximation; duration is the primary constraint.
    return len([t for t in (text or "").strip().split() if t])


def _count_sentences(text: str) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    return max(1, len(_SENTENCE_END_RE.findall(t)))


def _chunk_pieces_duration_first(
    *,
    pieces: list[TranscriptPiece],
    min_seconds: float = 20.0,
    max_seconds: float = 60.0,
    max_tokens_soft: int = 400,
    overlap_sentences: int = 2,
    overlap_max_pieces: int = 2,
    overlap_max_seconds: float = 15.0,
) -> list[tuple[float, float, str]]:
    """
    Duration-first chunking for video transcripts:
    - target ~20–60s per chunk (hard cap at 60s)
    - soft token bound to avoid huge chunks
    - small overlap (1–2 sentences) implemented via 1–2 tail pieces
    """
    out: list[tuple[float, float, str]] = []
    if not pieces:
        return out

    min_s = max(1.0, float(min_seconds))
    max_s = max(min_s, float(max_seconds))
    max_tokens = max(80, int(max_tokens_soft))
    overlap_n = max(0, int(overlap_sentences))
    overlap_piece_cap = max(0, int(overlap_max_pieces))
    overlap_sec_cap = max(0.0, float(overlap_max_seconds))

    buf: list[TranscriptPiece] = []
    tok = 0

    def buf_start_end() -> tuple[float, float]:
        if not buf:
            return (0.0, 0.0)
        return (float(buf[0].start_sec), float(buf[-1].end_sec))

    def flush():
        nonlocal buf, tok
        if not buf:
            return
        start, end = buf_start_end()
        txt = " ".join([(p.text or "").strip() for p in buf if (p.text or "").strip()]).strip()
        if txt:
            out.append((start, end, txt))

        # Prepare overlap seed for next chunk.
        if overlap_n <= 0 or overlap_piece_cap <= 0:
            buf = []
            tok = 0
            return

        tail: list[TranscriptPiece] = []
        sent = 0
        end0 = float(end)
        for p in reversed(buf):
            if len(tail) >= overlap_piece_cap:
                break
            dur = max(0.0, end0 - float(p.start_sec))
            if overlap_sec_cap and dur > overlap_sec_cap and tail:
                break
            tail.append(p)
            sent += _count_sentences(p.text)
            if sent >= overlap_n:
                break

        tail = list(reversed(tail))
        buf = tail
        tok = sum(_estimate_tokens(p.text) for p in buf)

    for p in pieces:
        t = (p.text or "").strip()
        if not t:
            continue

        # Oversized single piece: keep it alone.
        if float(p.end_sec) - float(p.start_sec) > max_s:
            if buf:
                flush()
            out.append((float(p.start_sec), float(p.end_sec), t))
            continue

        if not buf:
            buf = [p]
            tok = _estimate_tokens(t)
            continue

        start, end = buf_start_end()
        next_end = float(p.end_sec)
        dur_if_added = max(0.0, next_end - float(start))
        tok_if_added = tok + _estimate_tokens(t)

        dur_now = max(0.0, float(end) - float(start))

        # Hard duration cap.
        if dur_if_added > max_s:
            if dur_now >= min_s:
                flush()
            buf.append(p)
            tok += _estimate_tokens(t)
            continue

        # Soft token cap (only flush once we have enough duration).
        if tok_if_added > max_tokens and dur_now >= min_s:
            flush()
            buf.append(p)
            tok += _estimate_tokens(t)
            continue

        buf.append(p)
        tok = tok_if_added

        # Prefer tighter chunks once we're in a reasonable duration window.
        start, end = buf_start_end()
        dur_now = max(0.0, float(end) - float(start))
        if dur_now >= min_s and dur_now >= min(30.0, max_s):
            flush()

    if buf:
        flush()
    return out


async def ingest_video_asset_transcript_to_chunks(
    *,
    db: AsyncSession,
    video_asset_id: UUID,
    language_code: str,
) -> TranscriptChunkIngestResult:
    """
    Replace-all: write transcript chunks for a video into `content_chunks` so they participate
    in BM25 + pgvector retrieval.
    """
    settings: Settings = get_settings()
    if not getattr(settings, "rag_enabled", True):
        return TranscriptChunkIngestResult(chunks_written=0, embeddings_written=False)

    # Load asset + related content row (for category/title).
    ares = await db.execute(select(VideoAsset).where(VideoAsset.id == video_asset_id))
    asset = ares.scalar_one_or_none()
    if asset is None:
        return TranscriptChunkIngestResult(chunks_written=0, embeddings_written=False)

    cres = await db.execute(select(CourseContent).where(CourseContent.id == asset.content_id))
    content = cres.scalar_one_or_none()
    category = (content.category if content is not None else "media") or "media"
    title = (content.title if content is not None else None) or None

    # Best-effort: load chapters for this asset+language so we can link chunks to a chapter.
    # For now, the transcription pipeline writes a single fallback chapter ("Full Lecture").
    chapter_rows: list[VideoChapter] = []
    try:
        ch_res = await db.execute(
            select(VideoChapter)
            .where(VideoChapter.video_asset_id == asset.id, VideoChapter.language_code == language_code)
            .order_by(VideoChapter.chapter_index.asc(), VideoChapter.start_sec.asc())
        )
        chapter_rows = list(ch_res.scalars().all())
    except Exception:
        chapter_rows = []

    sres = await db.execute(
        select(TranscriptSegment)
        .where(
            TranscriptSegment.video_asset_id == asset.id,
            TranscriptSegment.language_code == language_code,
        )
        .order_by(TranscriptSegment.start_sec.asc())
    )
    segs = list(sres.scalars().all())
    pieces = [
        TranscriptPiece(start_sec=float(s.start_sec), end_sec=float(s.end_sec), text=str(s.text or ""))
        for s in segs
        if (s.text or "").strip()
    ]
    if not pieces:
        return TranscriptChunkIngestResult(chunks_written=0, embeddings_written=False)

    # Ensure we have at least one chapter row so we can reliably link chunks.
    if not chapter_rows:
        try:
            end_sec = max((float(p.end_sec) for p in pieces), default=0.0)
            await replace_with_fallback_chapter(
                db=db, video_asset_id=asset.id, language_code=language_code, end_sec=end_sec
            )
            ch_res = await db.execute(
                select(VideoChapter)
                .where(VideoChapter.video_asset_id == asset.id, VideoChapter.language_code == language_code)
                .order_by(VideoChapter.chapter_index.asc(), VideoChapter.start_sec.asc())
            )
            chapter_rows = list(ch_res.scalars().all())
        except Exception:
            chapter_rows = []

    # Chapter-aware, duration-first chunking (20–60s, small overlap).
    chapters = chapter_rows
    if not chapters:
        # In-memory fallback chapter (should be rare, only if DB write failed).
        chapters = [
            VideoChapter(
                video_asset_id=asset.id,
                language_code=str(language_code),
                chapter_index=0,
                start_sec=0.0,
                end_sec=max((float(p.end_sec) for p in pieces), default=0.0),
                title="Full Lecture",
                description=None,
            )
        ]

    chunks_with_chapter: list[tuple[VideoChapter | None, float, float, str, int]] = []
    for ch in chapters:
        ch_start = float(getattr(ch, "start_sec", 0.0) or 0.0)
        ch_end = float(getattr(ch, "end_sec", 0.0) or 0.0)
        ch_pieces = [p for p in pieces if float(p.end_sec) > ch_start and float(p.start_sec) < ch_end]
        if not ch_pieces:
            continue
        ch_chunks = _chunk_pieces_duration_first(pieces=ch_pieces, min_seconds=20.0, max_seconds=60.0)
        for j, (s, e, t) in enumerate(ch_chunks):
            chunks_with_chapter.append((ch, float(s), float(e), str(t), int(j)))

    if not chunks_with_chapter:
        # Last-resort: single chunk over whole transcript.
        s0 = float(pieces[0].start_sec)
        e0 = float(pieces[-1].end_sec)
        t0 = " ".join([(p.text or "").strip() for p in pieces if (p.text or "").strip()]).strip()
        chunks_with_chapter = [(chapters[0] if chapters else None, s0, e0, t0, 0)]

    # Best-effort embeddings for chunks.
    embeddings: list[list[float]] | None = None
    try:
        emb = get_embeddings(settings)
        embeddings = emb.embed_documents([c[3] for c in chunks_with_chapter])
    except Exception:
        embeddings = None

    # Replace-all semantics for this video content item.
    await db.execute(delete(ContentChunk).where(ContentChunk.content_id == asset.content_id))

    rows: list[ContentChunk] = []
    for i, (chapter, start_sec, end_sec, text, chunk_idx_in_chapter) in enumerate(chunks_with_chapter):
        meta: dict[str, Any] = {
            "doc_type": "segment",
            "source_kind": "video",
            "video_asset_id": str(asset.id),
            "start_sec": float(start_sec),
            "end_sec": float(end_sec),
            "language_code": str(language_code),
            "original_filename": asset.original_filename,
        }
        if title:
            meta["title"] = title
        if chapter is not None:
            # Keep a small chapter label in metadata for citation UX.
            meta["chapter_title"] = str(chapter.title or "").strip() or None

        row = ContentChunk(
            course_id=asset.course_id,
            content_id=asset.content_id,
            video_asset_id=asset.id,
            chapter_id=(chapter.id if chapter is not None and getattr(chapter, "id", None) else None),
            category=category,
            chunk_index=int(i),
            chunk_index_in_chapter=int(chunk_idx_in_chapter) if chapter is not None else None,
            chunk_start_sec=float(start_sec),
            chunk_end_sec=float(end_sec),
            text=text,
            meta=meta,
        )
        rows.append(row)

    embeddings_ok = bool(
        embeddings
        and len(embeddings) == len(rows)
        and all(isinstance(v, list) and len(v) == EMBEDDING_DIMS for v in embeddings)
    )
    if embeddings_ok:
        for row, vec in zip(rows, embeddings):
            row.embedding = _vector_literal(vec)

    db.add_all(rows)
    return TranscriptChunkIngestResult(chunks_written=len(rows), embeddings_written=embeddings_ok)


