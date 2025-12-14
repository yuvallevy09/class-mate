from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
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

    # JWT / cookies
    jwt_secret: str = Field(default="dev-change-me", validation_alias="JWT_SECRET")
    jwt_access_ttl_seconds: int = Field(default=900, validation_alias="JWT_ACCESS_TTL_SECONDS")
    jwt_refresh_ttl_seconds: int = Field(default=1209600, validation_alias="JWT_REFRESH_TTL_SECONDS")

    access_cookie_name: str = Field(default="access_token", validation_alias="ACCESS_COOKIE_NAME")

    cookie_secure: bool = Field(default=False, validation_alias="COOKIE_SECURE")
    cookie_domain: str | None = Field(default=None, validation_alias="COOKIE_DOMAIN")
    cookie_samesite: str = Field(default="lax", validation_alias="COOKIE_SAMESITE")

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


