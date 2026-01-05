from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.documents import Document

from app.core.settings import Settings
from app.rag.chroma import load_chroma
from app.rag.paths import course_persist_dir
from app.rag.types import RagHit


def course_index_exists(*, settings: Settings, user_id: int, course_id: UUID) -> bool:
    persist_dir = course_persist_dir(
        rag_store_dir=settings.rag_store_dir, user_id=user_id, course_id=course_id
    )
    return persist_dir.exists()


def retrieve_course_hits(
    *,
    settings: Settings,
    user_id: int,
    course_id: UUID,
    query: str,
    top_k: int,
    where: dict[str, Any] | None = None,
) -> list[RagHit]:
    persist_dir = course_persist_dir(
        rag_store_dir=settings.rag_store_dir, user_id=user_id, course_id=course_id
    )
    if not persist_dir.exists():
        return []

    store = load_chroma(
        persist_dir=persist_dir,
        settings=settings,
        collection_name=f"classmate_course_{course_id}",
    )

    # Returns list[(Document, score)]
    try:
        # langchain-chroma supports metadata filtering via `filter=...`
        pairs: list[tuple[Document, float]] = store.similarity_search_with_score(
            query,
            k=int(top_k),
            filter=(where or None),
        )
    except TypeError:
        # Older versions may not support the `filter` kwarg.
        pairs = store.similarity_search_with_score(query, k=int(top_k))
    hits: list[RagHit] = []
    seen: set[str] = set()
    for doc, score in pairs:
        meta: dict[str, Any] = dict(doc.metadata or {})
        text = str(doc.page_content or "")
        # De-dupe defensive: Chroma can occasionally return duplicate docs if the collection
        # contains legacy duplicates or mixed ID schemes.
        fp = "|".join(
            [
                str(meta.get("doc_type") or ""),
                str(meta.get("content_id") or ""),
                str(meta.get("video_asset_id") or ""),
                str(meta.get("start_sec") or ""),
                str(meta.get("end_sec") or ""),
                text[:200],
            ]
        )
        if fp in seen:
            continue
        seen.add(fp)
        hits.append(RagHit(text=text, metadata=meta, score=float(score)))
    return hits


