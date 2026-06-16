from __future__ import annotations

import asyncio
import traceback

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.settings import get_settings
from app.db.models.course import Course
from app.rag.course_retriever import CourseRetriever
from app.services.course_info import build_course_info_cached


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as db:
            res = await db.execute(select(Course).where(Course.name == "Algebra 1"))
            course = res.scalars().first()
            if course is None:
                print("no Algebra 1 course")
                return
            ci = await build_course_info_cached(db=db, course=course)
            print("lectures:", [(l.slug, l.transcript_ready) for l in ci.lectures])
            r = CourseRetriever()
            # Exact turn-2 params from the trace.
            try:
                decision = await r.retrieve(
                    db=db,
                    course_info=ci,
                    contextualized_query="Can you explain De Moivre's theorem, which was introduced in Lecture 8 on Powers & Roots in Polar form?",
                    lecture_routing=["L8"],
                    target_lecture_slug="None",
                    target_timestamp="None",
                )
                print("OK path=", decision.path, "ndocs=", len(decision.docs))
            except Exception:
                print("RETRIEVE RAISED:")
                traceback.print_exc()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
