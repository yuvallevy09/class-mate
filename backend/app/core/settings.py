from __future__ import annotations

from functools import lru_cache
from typing import List, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

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
    upload_max_size_bytes: int = Field(default=26214400, validation_alias="UPLOAD_MAX_SIZE_BYTES")

    # LLM (Gemini)
    # We read both, but the caller should choose deterministically and pass api_key explicitly.
    google_api_key: str | None = Field(default=None, validation_alias="GOOGLE_API_KEY")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_MODEL")
    chat_history_max_messages: int = Field(default=12, validation_alias="CHAT_HISTORY_MAX_MESSAGES")
    chat_temperature: float = Field(default=0.0, validation_alias="CHAT_TEMPERATURE")

    # RAG (local-first, persisted to disk)
    rag_enabled: bool = Field(default=True, validation_alias="RAG_ENABLED")
    rag_store_dir: str = Field(default=".rag_store", validation_alias="RAG_STORE_DIR")
    rag_top_k: int = Field(default=4, validation_alias="RAG_TOP_K")
    rag_chunk_size: int = Field(default=1200, validation_alias="RAG_CHUNK_SIZE")
    rag_chunk_overlap: int = Field(default=200, validation_alias="RAG_CHUNK_OVERLAP")
    rag_embedding_model: str = Field(default="models/embedding-001", validation_alias="RAG_EMBEDDING_MODEL")

    # RAG embeddings provider
    # - "gemini": uses GoogleGenerativeAIEmbeddings (requires GOOGLE_API_KEY/GEMINI_API_KEY + quota)
    # - "hf": uses HuggingFaceEmbeddings (local, no quota; heavier dependency)
    rag_embeddings_provider: Literal["gemini", "hf"] = Field(
        default="gemini", validation_alias="RAG_EMBEDDINGS_PROVIDER"
    )
    rag_local_embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        validation_alias="RAG_LOCAL_EMBEDDING_MODEL",
    )

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
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


