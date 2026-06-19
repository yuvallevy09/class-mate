from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # IMPORTANT: load env relative to the backend folder, not the current working directory.
    # This avoids common CORS/auth misconfig when starting uvicorn from the repo root.
    _BACKEND_ROOT = Path(__file__).resolve().parents[2]  # backend/
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_ROOT / ".env"),
        env_ignore_empty=True,
        extra="ignore",
    )

    # Environment
    environment: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="ENVIRONMENT"
    )

    # Server
    port: int = Field(default=3001, validation_alias="PORT")

    # CORS
    cors_origins: List[str] = Field(default_factory=list, validation_alias="CORS_ORIGINS")

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/classmate",
        validation_alias="DATABASE_URL",
    )

    # S3 uploads (presigned)
    s3_endpoint_url: str | None = Field(default=None, validation_alias="S3_ENDPOINT_URL")
    s3_region: str = Field(default="us-east-1", validation_alias="S3_REGION")
    s3_bucket: str | None = Field(default=None, validation_alias="S3_BUCKET")
    s3_access_key_id: str | None = Field(default=None, validation_alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str | None = Field(default=None, validation_alias="S3_SECRET_ACCESS_KEY")
    s3_presign_expires_seconds: int = Field(default=900, validation_alias="S3_PRESIGN_EXPIRES_SECONDS")
    s3_download_expires_seconds: int = Field(default=300, validation_alias="S3_DOWNLOAD_EXPIRES_SECONDS")
    s3_audio_presign_expires_seconds: int = Field(
        default=3600, validation_alias="S3_AUDIO_PRESIGN_EXPIRES_SECONDS"
    )
    upload_max_size_bytes: int = Field(default=26214400, validation_alias="UPLOAD_MAX_SIZE_BYTES")

    # ffmpeg (video -> audio/thumbnail)
    ffmpeg_bin: str = Field(default="ffmpeg", validation_alias="FFMPEG_BIN")
    ffprobe_bin: str = Field(default="ffprobe", validation_alias="FFPROBE_BIN")
    thumbnail_seek_seconds: float = Field(default=1.0, validation_alias="THUMBNAIL_SEEK_SECONDS")
    # For longer videos the opening frame is often a black/intro frame, so seek
    # further in. Videos at/above this duration use thumbnail_long_seek_seconds.
    thumbnail_long_video_min_seconds: float = Field(
        default=300.0, validation_alias="THUMBNAIL_LONG_VIDEO_MIN_SECONDS"
    )
    thumbnail_long_seek_seconds: float = Field(
        default=60.0, validation_alias="THUMBNAIL_LONG_SEEK_SECONDS"
    )

    # Runpod serverless (faster-whisper)
    runpod_api_key: str | None = Field(default=None, validation_alias="RUNPOD_API_KEY")
    runpod_endpoint_id: str | None = Field(default=None, validation_alias="RUNPOD_ENDPOINT_ID")
    runpod_poll_interval_seconds: float = Field(default=2.0, validation_alias="RUNPOD_POLL_INTERVAL_SECONDS")
    # Overall budget for a transcription job (used as the poll deadline in /run mode,
    # and as the blocking HTTP timeout in /runsync mode). Long lectures need headroom.
    runpod_timeout_seconds: float = Field(default=1800.0, validation_alias="RUNPOD_TIMEOUT_SECONDS")
    # Per-request HTTP timeout when polling (/run mode): each /run + /status call returns
    # quickly, so this stays small and independent of the overall job budget above.
    runpod_http_timeout_seconds: float = Field(default=120.0, validation_alias="RUNPOD_HTTP_TIMEOUT_SECONDS")
    runpod_use_runsync: bool = Field(default=True, validation_alias="RUNPOD_USE_RUNSYNC")
    runpod_whisper_model: str = Field(default="base", validation_alias="RUNPOD_WHISPER_MODEL")

    # LLM provider selection
    # - "gemini": gemini_model via GOOGLE_API_KEY (dev default)
    # - "anthropic": anthropic_model via ANTHROPIC_API_KEY (prod)
    # Flip LLM_PROVIDER=anthropic (and set ANTHROPIC_API_KEY) to switch.
    llm_provider: Literal["gemini", "anthropic"] = Field(default="gemini", validation_alias="LLM_PROVIDER")
    google_api_key: str | None = Field(default=None, validation_alias="GOOGLE_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_MODEL")
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-6", validation_alias="ANTHROPIC_MODEL")

    # Per-task model selection (see app/ai/model_roles.py). When enabled, each
    # pipeline step uses a model tiered to its job (Flash/Haiku/Sonnet) instead
    # of the single global provider above; any tier whose provider key is unset
    # falls back to the global provider. Set MODEL_ROLES_ENABLED=false to revert
    # to the single-provider path. Haiku is the only extra model string needed
    # (Flash reuses gemini_model, Sonnet reuses anthropic_model).
    model_roles_enabled: bool = Field(default=True, validation_alias="MODEL_ROLES_ENABLED")
    anthropic_haiku_model: str = Field(default="claude-haiku-4-5", validation_alias="ANTHROPIC_HAIKU_MODEL")

    # Semantic chapterization (LLM-segmented chapters). OFF by default: chapters are
    # not yet surfaced in the player UI and only lightly affect retrieval, so the LLM
    # call isn't worth running. When false, the pipeline writes the single
    # "Full Lecture" fallback chapter without calling the model. Flip to true to
    # generate real chapters (the output token budget is already sized for it).
    chapters_enabled: bool = Field(default=False, validation_alias="CHAPTERS_ENABLED")

    # OpenAI (embeddings)
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    chat_history_max_messages: int = Field(default=12, validation_alias="CHAT_HISTORY_MAX_MESSAGES")
    chat_temperature: float = Field(default=0.0, validation_alias="CHAT_TEMPERATURE")

    # RAG (retrieval corpus lives in Postgres/pgvector)
    rag_enabled: bool = Field(default=True, validation_alias="RAG_ENABLED")
    rag_top_k: int = Field(default=4, validation_alias="RAG_TOP_K")
    rag_chunk_size: int = Field(default=1200, validation_alias="RAG_CHUNK_SIZE")
    rag_chunk_overlap: int = Field(default=200, validation_alias="RAG_CHUNK_OVERLAP")
    # OpenAI embeddings only. Dimensionality must match EMBEDDING_DIMS (pgvector
    # schema) — changing models to a different dimension requires a migration + reindex.
    rag_embedding_model: str = Field(default="text-embedding-3-small", validation_alias="RAG_EMBEDDING_MODEL")

    # DSPy tracing (MLflow autolog; dev-only observability for DSPy modules).
    # Requires the `mlflow` dev dependency; ignored in production installs without it.
    dspy_tracing_enabled: bool = Field(default=False, validation_alias="DSPY_TRACING_ENABLED")

    # JWT / cookies
    jwt_secret: str = Field(default="dev-change-me", validation_alias="JWT_SECRET")
    jwt_access_ttl_seconds: int = Field(default=900, validation_alias="JWT_ACCESS_TTL_SECONDS")
    jwt_refresh_ttl_seconds: int = Field(default=1209600, validation_alias="JWT_REFRESH_TTL_SECONDS")

    access_cookie_name: str = Field(default="access_token", validation_alias="ACCESS_COOKIE_NAME")
    refresh_cookie_name: str = Field(default="refresh_token", validation_alias="REFRESH_COOKIE_NAME")
    refresh_cookie_path: str = Field(default="/api/v1/auth", validation_alias="REFRESH_COOKIE_PATH")

    cookie_secure: bool = Field(default=False, validation_alias="COOKIE_SECURE")
    cookie_domain: str | None = Field(default=None, validation_alias="COOKIE_DOMAIN")
    cookie_samesite: Literal["lax", "strict", "none"] = Field(default="lax", validation_alias="COOKIE_SAMESITE")

    # CSRF (double-submit cookie)
    csrf_enabled: bool = Field(default=True, validation_alias="CSRF_ENABLED")
    csrf_cookie_name: str = Field(default="csrf_token", validation_alias="CSRF_COOKIE_NAME")
    csrf_header_name: str = Field(default="X-CSRF-Token", validation_alias="CSRF_HEADER_NAME")
    csrf_cookie_path: str = Field(default="/", validation_alias="CSRF_COOKIE_PATH")
    csrf_cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax", validation_alias="CSRF_COOKIE_SAMESITE"
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v):
        # Allow:
        # - comma-separated string: "http://a,http://b"
        # - JSON array: '["http://a","http://b"]' (pydantic will parse it before this in many cases)
        # - already-a-list
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @model_validator(mode="after")
    def _validate_cookie_policy(self) -> "Settings":
        # If SameSite=None, browsers require Secure=true for cookies to be accepted.
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when COOKIE_SAMESITE is 'none'")
        if self.csrf_cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when CSRF_COOKIE_SAMESITE is 'none'")
        if self.s3_presign_expires_seconds <= 0:
            raise ValueError("S3_PRESIGN_EXPIRES_SECONDS must be > 0")
        if self.s3_download_expires_seconds <= 0:
            raise ValueError("S3_DOWNLOAD_EXPIRES_SECONDS must be > 0")
        if self.s3_audio_presign_expires_seconds <= 0:
            raise ValueError("S3_AUDIO_PRESIGN_EXPIRES_SECONDS must be > 0")
        if self.upload_max_size_bytes <= 0:
            raise ValueError("UPLOAD_MAX_SIZE_BYTES must be > 0")
        if self.chat_history_max_messages <= 0:
            raise ValueError("CHAT_HISTORY_MAX_MESSAGES must be > 0")
        if not (0.0 <= float(self.chat_temperature) <= 2.0):
            raise ValueError("CHAT_TEMPERATURE must be between 0 and 2")
        if self.rag_top_k <= 0:
            raise ValueError("RAG_TOP_K must be > 0")
        if self.rag_chunk_size <= 0:
            raise ValueError("RAG_CHUNK_SIZE must be > 0")
        if self.rag_chunk_overlap < 0:
            raise ValueError("RAG_CHUNK_OVERLAP must be >= 0")
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP must be < RAG_CHUNK_SIZE")
        if float(self.thumbnail_seek_seconds) < 0.0:
            raise ValueError("THUMBNAIL_SEEK_SECONDS must be >= 0")
        if float(self.runpod_poll_interval_seconds) <= 0:
            raise ValueError("RUNPOD_POLL_INTERVAL_SECONDS must be > 0")
        if float(self.runpod_timeout_seconds) <= 0:
            raise ValueError("RUNPOD_TIMEOUT_SECONDS must be > 0")
        if float(self.runpod_http_timeout_seconds) <= 0:
            raise ValueError("RUNPOD_HTTP_TIMEOUT_SECONDS must be > 0")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


