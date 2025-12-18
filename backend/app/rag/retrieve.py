from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.documents import Document

from app.core.settings import Settings
from app.rag.paths import course_persist_dir
from app.rag.types import RagHit


def _get_embeddings(settings: Settings):
    """
    Prefer Gemini embeddings to match the chat provider.
    If not configured, raise ValueError so callers can fallback gracefully.
    """
    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ValueError("GoogleGenerativeAIEmbeddings not available") from e

    api_key = (settings.google_api_key or "").strip() or (settings.gemini_api_key or "").strip()
    if not api_key:
        raise ValueError("Missing Gemini API key for embeddings")

    # LLM model is configurable already; keep embedding model separately configurable for safety.
    model = (getattr(settings, "rag_embedding_model", None) or "").strip() or "models/embedding-001"
    return GoogleGenerativeAIEmbeddings(model=model, google_api_key=api_key)


def _load_chroma(*, persist_dir: Path, settings: Settings, collection_name: str):
    try:
        from langchain_community.vectorstores import Chroma  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ValueError("Chroma vectorstore integration not available") from e

    embeddings = _get_embeddings(settings)
    return Chroma(
        collection_name=collection_name,
        persist_directory=str(persist_dir),
        embedding_function=embeddings,
    )


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
) -> list[RagHit]:
    persist_dir = course_persist_dir(
        rag_store_dir=settings.rag_store_dir, user_id=user_id, course_id=course_id
    )
    if not persist_dir.exists():
        return []

    store = _load_chroma(
        persist_dir=persist_dir,
        settings=settings,
        collection_name=f"classmate_course_{course_id}",
    )

    # Returns list[(Document, score)]
    pairs: list[tuple[Document, float]] = store.similarity_search_with_score(query, k=int(top_k))
    hits: list[RagHit] = []
    for doc, score in pairs:
        meta: dict[str, Any] = dict(doc.metadata or {})
        hits.append(RagHit(text=str(doc.page_content or ""), metadata=meta, score=float(score)))
    return hits


