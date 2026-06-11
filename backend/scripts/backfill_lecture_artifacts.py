from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running as a plain file (`python scripts/backfill_lecture_artifacts.py`)
# by putting the backend root on sys.path so `import app...` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.settings import get_settings
from app.core.tracing import setup_dspy_tracing

# Import related models so SQLAlchemy can resolve mapper references (User/Course/etc).
from app.db.models.user import User  # noqa: F401
from app.db.models.course import Course  # noqa: F401
from app.db.models.course_content import CourseContent  # noqa: F401
from app.db.models.video_asset import VideoAsset
from app.services.course_summary import generate_and_store_course_summary
from app.services.lecture_artifacts import generate_and_store_lecture_artifacts

# Statuses with a usable transcript; matches the lazy backfill in the summary endpoint.
DONE_STATUSES = ("done", "done_no_embeddings", "done_no_index")


async def main() -> None:
    """Generate AI artifacts (title/description/summary) for transcribed assets missing them."""
    settings = get_settings()
    if settings.dspy_tracing_enabled:
        setup_dspy_tracing()

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
            else:
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

            # Course summaries: generate for courses that don't have one yet.
            # The service no-ops for courses with no usable lecture summaries.
            cres = await session.execute(select(Course).where(Course.ai_summary.is_(None)))
            courses = list(cres.scalars().all())
            if not courses:
                print("No courses need a summary.")
                return

            summarized = 0
            for course in courses:
                try:
                    out = await generate_and_store_course_summary(
                        db=session,
                        settings=settings,
                        course_id=course.id,
                    )
                    if out is not None and out.ai_summary:
                        summarized += 1
                        print(f"OK   course {course.id}")
                    else:
                        print(f"SKIP course {course.id} (no usable lecture summaries)")
                except Exception as e:
                    await session.rollback()
                    print(f"FAIL course {course.id}: {e}")

            print(f"Done: {summarized}/{len(courses)} course summaries generated.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
