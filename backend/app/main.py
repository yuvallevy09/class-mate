from fastapi import FastAPI
from fastapi import Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.session import get_db


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="ClassMate API")

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/health/db")
    async def health_db(db: AsyncSession = Depends(get_db)):
        await db.execute(text("SELECT 1"))
        return {"ok": True}

    return app


app = create_app()


