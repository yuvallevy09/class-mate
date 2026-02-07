from __future__ import annotations

from dataclasses import dataclass
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

    chunks = _chunk_transcript(
        pieces=pieces,
        chunk_size=int(getattr(settings, "rag_chunk_size", 1200)),
        overlap_chars=int(getattr(settings, "rag_chunk_overlap", 200)),
    )

    # Best-effort embeddings for chunks.
    embeddings: list[list[float]] | None = None
    try:
        emb = get_embeddings(settings)
        embeddings = emb.embed_documents([c[2] for c in chunks])
    except Exception:
        embeddings = None

    # Replace-all semantics for this video content item.
    await db.execute(delete(ContentChunk).where(ContentChunk.content_id == asset.content_id))

    rows: list[ContentChunk] = []
    for i, (start_sec, end_sec, text) in enumerate(chunks):
        # Assign a chapter by midpoint timestamp (best-effort).
        chapter: VideoChapter | None = None
        try:
            mid = (float(start_sec) + float(end_sec)) / 2.0
        except Exception:
            mid = float(start_sec or 0.0)
        for ch in chapter_rows:
            if float(ch.start_sec) <= mid <= float(ch.end_sec):
                chapter = ch
                break
        if chapter is None and chapter_rows:
            chapter = chapter_rows[0]

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
            chapter_id=(chapter.id if chapter is not None else None),
            category=category,
            chunk_index=int(i),
            chunk_index_in_chapter=int(i) if chapter is not None else None,
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


