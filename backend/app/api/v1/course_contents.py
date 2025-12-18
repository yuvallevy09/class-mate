from __future__ import annotations

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
from app.db.models.user import User
from app.db.session import get_db
from app.rag.ingest import index_course_for_user
from app.schemas.course_content import CourseContentCreate, CourseContentPublic

router = APIRouter(tags=["course-contents"])

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
        stmt = stmt.where(CourseContent.category == category)
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
    db.add(content)
    await db.commit()
    await db.refresh(content)

    # Demo-friendly ingestion trigger (PDF-only handled inside the indexer).
    # BackgroundTasks runs in-process; heavy work is isolated from the request DB session.
    if content.file_key and settings.rag_enabled:
        background_tasks.add_task(index_course_for_user, user_id=current_user.id, course_id=course_id)

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

    # Best-effort storage cleanup: if there's an S3 object, delete it before deleting the DB row.
    # If the object is already missing, treat as success. For other S3 errors, fail fast so
    # callers can retry and we don't orphan storage.
    if content.file_key:
        if not settings.s3_bucket:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="S3 is not configured (missing S3_BUCKET); cannot delete stored file",
            )
        s3 = _s3_client(settings)
        try:
            s3.delete_object(Bucket=settings.s3_bucket, Key=content.file_key)
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404", "NotFound"}:
                pass
            else:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to delete file from S3",
                ) from e

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


