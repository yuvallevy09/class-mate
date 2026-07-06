from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.s3 import s3_client
from app.core.settings import get_settings

# Import related models so SQLAlchemy can resolve mapper references (User/Course/etc).
from app.db.models.user import User  # noqa: F401
from app.db.models.course import Course  # noqa: F401
from app.db.models.course_content import CourseContent  # noqa: F401
from app.db.models.video_asset import VideoAsset
from app.services.transcription import extract_and_upload_thumbnail


async def main() -> None:
    """Generate thumbnails for existing video assets that don't have one yet."""
    settings = get_settings()
    if not settings.s3_bucket:
        raise RuntimeError("S3_BUCKET missing")

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    s3 = s3_client(settings)

    try:
        async with SessionLocal() as session:
            res = await session.execute(
                select(VideoAsset).where(
                    VideoAsset.thumbnail_file_key.is_(None),
                    VideoAsset.source_file_key.is_not(None),
                )
            )
            assets = list(res.scalars().all())
            if not assets:
                print("No video assets need a thumbnail.")
                return

            done = 0
            for asset in assets:
                try:
                    with tempfile.TemporaryDirectory() as td:
                        video_path = Path(td) / "input.video"

                        def _download():
                            with video_path.open("wb") as f:
                                s3.download_fileobj(settings.s3_bucket, asset.source_file_key, f)

                        await asyncio.to_thread(_download)
                        key = await asyncio.to_thread(
                            extract_and_upload_thumbnail,
                            s3=s3,
                            settings=settings,
                            video_path=video_path,
                            course_id=asset.course_id,
                            video_asset_id=asset.id,
                        )
                    asset.thumbnail_file_key = key
                    await session.commit()
                    done += 1
                    print(f"OK   {asset.id} -> {key}")
                except Exception as e:
                    await session.rollback()
                    print(f"FAIL {asset.id}: {e}")

            print(f"Done: {done}/{len(assets)} thumbnails generated.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
