from __future__ import annotations

from app.core.settings import Settings


def get_embeddings(settings: Settings):
    """
    Embeddings provider:
    - Gemini (remote): requires GOOGLE_API_KEY/GEMINI_API_KEY and quota.
    - OpenAI (remote): requires OPENAI_API_KEY.
    - HuggingFace (local): no quota, but requires heavier deps + model download.

    Raises ValueError if not configured/available; callers should treat as "embeddings disabled".
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

    if provider == "openai":
        try:
            from langchain_openai import OpenAIEmbeddings  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ValueError("OpenAIEmbeddings not available") from e

        api_key = (getattr(settings, "openai_api_key", None) or "").strip()
        if not api_key:
            raise ValueError("Missing OpenAI API key for embeddings")

        model = (getattr(settings, "rag_embedding_model", None) or "").strip() or "text-embedding-3-small"
        return OpenAIEmbeddings(model=model, api_key=api_key)

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


