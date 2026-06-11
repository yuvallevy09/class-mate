from __future__ import annotations

from app.core.settings import Settings


def get_embeddings(settings: Settings):
    """
    OpenAI embeddings (requires OPENAI_API_KEY).

    The embedding model's dimensionality must match EMBEDDING_DIMS in
    `app.rag.embedding_config` (pgvector schema is fixed at vector(d));
    text-embedding-3-small is 1536 dims.

    Raises ValueError if not configured/available; callers should treat as
    "embeddings disabled".
    """
    try:
        from langchain_openai import OpenAIEmbeddings  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ValueError("OpenAIEmbeddings not available") from e

    api_key = (getattr(settings, "openai_api_key", None) or "").strip()
    if not api_key:
        raise ValueError("Missing OpenAI API key for embeddings")

    model = (getattr(settings, "rag_embedding_model", None) or "").strip() or "text-embedding-3-small"
    return OpenAIEmbeddings(model=model, api_key=api_key)
