from __future__ import annotations

import logging
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.settings import Settings, get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.video_asset import VideoAsset
from app.db.models.user import User
from app.db.session import get_db
from app.services.content_ingestion import ingest_content_to_db
from app.schemas.course_content import CourseContentCreate, CourseContentPublic

router = APIRouter(tags=["course-contents"])
logger = logging.getLogger(__name__)

class DownloadUrlResponse(BaseModel):
    url: str


def _s3_client(settings: Settings):
    kwargs: dict = {"service_name": "s3", "region_name": settings.s3_region}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client(**kwargs)


async def _get_owned_course(db: AsyncSession, *, course_id: UUID, user_id: int) -> Course:
    res = await db.execute(select(Course).where(Course.id == course_id, Course.user_id == user_id))
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


@router.get("/courses/{course_id}/contents", response_model=list[CourseContentPublic])
async def list_course_contents(
    course_id: UUID,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CourseContent]:
    await _get_owned_course(db, course_id=course_id, user_id=current_user.id)

    stmt = select(CourseContent).where(CourseContent.course_id == course_id)
    if category:
        stmt = stmt.where(CourseContent.category == (category or "").strip())
    stmt = stmt.order_by(CourseContent.created_at.desc())

    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.post("/courses/{course_id}/contents", response_model=CourseContentPublic)
async def create_course_content(
    course_id: UUID,
    body: CourseContentCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> CourseContent:
    await _get_owned_course(db, course_id=course_id, user_id=current_user.id)

    if body.file_key and not settings.s3_bucket:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="S3 is not configured (missing S3_BUCKET); cannot attach file to content",
        )

    content = CourseContent(
        course_id=course_id,
        category=body.category,
        title=body.title,
        description=body.description,
        file_key=body.file_key,
        original_filename=body.original_filename,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
    )
    # If this item has an attached file and ingestion is enabled, mark as queued.
    if body.file_key and settings.rag_enabled:
        content.ingestion_status = "queued"
    db.add(content)
    await db.commit()
    await db.refresh(content)

    # Ingestion trigger: build Postgres retrieval corpus for PDF uploads.
    # BackgroundTasks runs in-process; heavy work is isolated from the request DB session.
    if content.file_key and settings.rag_enabled:
        background_tasks.add_task(ingest_content_to_db, content_id=content.id)

    return content


@router.delete("/contents/{content_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course_content(
    content_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Response:
    res = await db.execute(
        select(CourseContent, Course)
        .join(Course, Course.id == CourseContent.course_id)
        .where(CourseContent.id == content_id, Course.user_id == current_user.id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")

    content: CourseContent = row[0]

    # Storage cleanup policy (best-effort):
    # - DB is the source of truth; always delete DB rows.
    # - Attempt to delete S3 objects (ignore failures to avoid blocking deletes).
    keys_to_delete: list[str] = []
    if content.file_key:
        keys_to_delete.append(content.file_key)

    # If this is a video content item, also attempt cleanup of derived artifacts (audio_file_key)
    # and any source_file_key if it differs from the content file key.
    mt = (content.mime_type or "").strip().lower()
    if content.category == "media" and mt.startswith("video/"):
        vres = await db.execute(select(VideoAsset).where(VideoAsset.content_id == content.id))
        asset = vres.scalar_one_or_none()
        if asset is not None:
            if asset.source_file_key and asset.source_file_key not in keys_to_delete:
                keys_to_delete.append(asset.source_file_key)
            if asset.audio_file_key and asset.audio_file_key not in keys_to_delete:
                keys_to_delete.append(asset.audio_file_key)

    if keys_to_delete and settings.s3_bucket:
        s3 = _s3_client(settings)
        for key in keys_to_delete:
            try:
                s3.delete_object(Bucket=settings.s3_bucket, Key=key)
            except ClientError as e:
                code = (e.response or {}).get("Error", {}).get("Code")
                # Ignore missing objects and transient failures; log for observability.
                if code not in {"NoSuchKey", "404", "NotFound"}:
                    logger.warning("S3 delete failed (best-effort): %s", str(e))
            except Exception as e:
                logger.warning("S3 delete failed (best-effort): %s", str(e))

    await db.delete(content)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/contents/{content_id}/download", response_model=DownloadUrlResponse)
async def get_download_url(
    content_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> DownloadUrlResponse:
    if not settings.s3_bucket:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="S3 is not configured (missing S3_BUCKET)",
        )

    res = await db.execute(
        select(CourseContent, Course)
        .join(Course, Course.id == CourseContent.course_id)
        .where(CourseContent.id == content_id, Course.user_id == current_user.id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")

    content: CourseContent = row[0]
    if not content.file_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No file for this content")

    s3 = _s3_client(settings)
    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.s3_bucket, "Key": content.file_key},
        ExpiresIn=int(settings.s3_download_expires_seconds),
    )
    return DownloadUrlResponse(url=url)


