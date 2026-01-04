import os

from fastapi import FastAPI
from fastapi import Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.router import api_router
from app.core.settings import get_settings
from app.db.session import get_db


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="ClassMate API")

    csrf_allowlist: set[tuple[str, str]] = {
        ("GET", "/health"),
        ("GET", "/health/db"),
        ("GET", "/api/v1/auth/csrf"),
    }

    @app.middleware("http")
    async def csrf_middleware(request: Request, call_next):
        s = get_settings()

        method = request.method.upper()
        path = request.url.path

        if not s.csrf_enabled:
            return await call_next(request)

        # Webhooks are CSRF-exempt (third-party callbacks cannot send CSRF headers).
        if path.startswith("/api/webhooks/"):
            return await call_next(request)

        if (method, path) in csrf_allowlist:
            return await call_next(request)

        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            csrf_cookie = request.cookies.get(s.csrf_cookie_name)
            csrf_header = request.headers.get(s.csrf_header_name)
            if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
                return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)

        return await call_next(request)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
        )

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/health/db")
    async def health_db(db: AsyncSession = Depends(get_db)):
        await db.execute(text("SELECT 1"))
        return {"ok": True}

    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()


