from __future__ import annotations

from pathlib import Path
from typing import Literal

from app.core.settings import Settings


def get_embeddings(settings: Settings):
    """
    Embeddings provider:
    - Gemini (remote): requires GOOGLE_API_KEY/GEMINI_API_KEY and quota.
    - HuggingFace (local): no quota, but requires heavier deps + model download.

    Raises ValueError if not configured/available; callers should treat as "indexing disabled".
    """
    provider = (getattr(settings, "rag_embeddings_provider", "gemini") or "gemini").strip().lower()

    if provider == "hf":
        try:
            from langchain_huggingface import HuggingFaceEmbeddings  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ValueError("HuggingFaceEmbeddings not available") from e

        model_name = (
            (getattr(settings, "rag_local_embedding_model", None) or "").strip()
            or "sentence-transformers/all-MiniLM-L6-v2"
        )
        return HuggingFaceEmbeddings(model_name=model_name, encode_kwargs={"normalize_embeddings": True})

    # Default: Gemini embeddings
    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ValueError("GoogleGenerativeAIEmbeddings not available") from e

    api_key = (settings.google_api_key or "").strip() or (settings.gemini_api_key or "").strip()
    if not api_key:
        raise ValueError("Missing Gemini API key for embeddings")

    model = (getattr(settings, "rag_embedding_model", None) or "").strip() or "models/embedding-001"
    return GoogleGenerativeAIEmbeddings(model=model, google_api_key=api_key)


def load_chroma(*, persist_dir: Path, settings: Settings, collection_name: str):
    """
    Return a LangChain Chroma vector store wired to the configured embeddings provider.
    """
    try:
        from langchain_chroma import Chroma  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ValueError("Chroma vectorstore integration not available") from e

    embeddings = get_embeddings(settings)
    return Chroma(
        collection_name=collection_name,
        persist_directory=str(persist_dir),
        embedding_function=embeddings,
    )


