from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.settings import Settings, get_settings
from app.db.models.course import Course
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.video_asset import VideoAsset
from app.db.session import get_session_maker
from app.rag.chroma import get_embeddings, load_chroma
from app.rag.paths import course_persist_dir


async def index_video_asset_segments(*, video_asset_id: UUID) -> None:
    """
    Upsert Bunny transcript segments into the per-course persisted Chroma collection.

    This is best-effort:
    - If embeddings aren't configured, it no-ops (DB remains the source of truth).
    - If Chroma isn't available, it no-ops.
    """
    settings: Settings = get_settings()
    if not getattr(settings, "rag_enabled", True):
        return

    # If embeddings aren't configured, skip indexing (chat can still work via DB fallback later).
    try:
        get_embeddings(settings)
    except ValueError:
        return

    SessionLocal = get_session_maker()
    async with SessionLocal() as db:
        res = await db.execute(select(VideoAsset).where(VideoAsset.id == video_asset_id))
        asset = res.scalar_one_or_none()
        if asset is None:
            return

        # Resolve user_id for the persist dir (course indexes are namespaced per user in this codebase).
        cres = await db.execute(select(Course).where(Course.id == asset.course_id))
        course = cres.scalar_one_or_none()
        if course is None:
            return

        sres = await db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.video_asset_id == asset.id)
            .order_by(TranscriptSegment.start_sec.asc())
        )
        segments = list(sres.scalars().all())

    if not segments:
        return

    persist_dir = course_persist_dir(
        rag_store_dir=settings.rag_store_dir,
        user_id=int(course.user_id),
        course_id=asset.course_id,
    )
    persist_dir.mkdir(parents=True, exist_ok=True)

    try:
        store = load_chroma(
            persist_dir=persist_dir,
            settings=settings,
            collection_name=f"classmate_course_{asset.course_id}",
        )
    except Exception:
        return

    from langchain_core.documents import Document

    docs: list[Document] = []
    ids: list[str] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        meta: dict[str, Any] = {
            "doc_type": "segment",
            "course_id": str(seg.course_id),
            "video_asset_id": str(seg.video_asset_id),
            "video_guid": str(asset.video_guid),
            "start_sec": float(seg.start_sec),
            "end_sec": float(seg.end_sec),
            "language_code": str(seg.language_code),
        }
        docs.append(Document(page_content=text, metadata=meta))
        ids.append(f"segment:{seg.id}")

    if not docs:
        return

    # Best-effort upsert behavior:
    # 1) delete any prior segment docs for this video (handles legacy IDs / duplicates)
    # 2) delete by IDs as a fallback
    # 3) add fresh docs
    try:
        # Prefer metadata-based delete to clean out any older IDs/prefixes.
        coll = getattr(store, "_collection", None)
        if coll is not None:
            coll.delete(where={"doc_type": "segment", "video_asset_id": str(asset.id)})
    except Exception:
        pass
    try:
        store.delete(ids=ids)
    except Exception:
        pass

    try:
        store.add_documents(docs, ids=ids)
    except Exception:
        # If vector insertion fails (quota, etc.), keep DB truth and allow retry later.
        return


