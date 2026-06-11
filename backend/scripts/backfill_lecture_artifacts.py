from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.settings import get_settings

# Import related models so SQLAlchemy can resolve mapper references (User/Course/etc).
from app.db.models.user import User  # noqa: F401
from app.db.models.course import Course  # noqa: F401
from app.db.models.course_content import CourseContent  # noqa: F401
from app.db.models.video_asset import VideoAsset
from app.services.lecture_artifacts import generate_and_store_lecture_artifacts

# Statuses with a usable transcript; matches the lazy backfill in the summary endpoint.
DONE_STATUSES = ("done", "done_no_embeddings", "done_no_index")


async def main() -> None:
    """Generate AI artifacts (title/description/summary) for transcribed assets missing them."""
    settings = get_settings()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with SessionLocal() as session:
            res = await session.execute(
                select(VideoAsset).where(
                    VideoAsset.status.in_(DONE_STATUSES),
                    VideoAsset.ai_description.is_(None),
                )
            )
            assets = list(res.scalars().all())
            if not assets:
                print("No video assets need artifacts.")
                return

            done = 0
            for asset in assets:
                try:
                    await generate_and_store_lecture_artifacts(
                        db=session,
                        settings=settings,
                        video_asset_id=asset.id,
                        force=False,
                    )
                    done += 1
                    print(f"OK   {asset.id}")
                except Exception as e:
                    await session.rollback()
                    print(f"FAIL {asset.id}: {e}")

            print(f"Done: {done}/{len(assets)} assets backfilled.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
