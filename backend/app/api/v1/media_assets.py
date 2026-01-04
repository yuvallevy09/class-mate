from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.settings import Settings, get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.db.session import get_db
from app.schemas.media_asset import MediaAssetCreate, MediaAssetPublic

router = APIRouter(tags=["media-assets"])


async def _get_owned_course(db: AsyncSession, *, course_id: UUID, user_id: int) -> Course:
    res = await db.execute(select(Course).where(Course.id == course_id, Course.user_id == user_id))
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


async def _validate_content_link(
    db: AsyncSession, *, content_id: UUID, course_id: UUID, user_id: int
) -> CourseContent:
    res = await db.execute(
        select(CourseContent, Course)
        .join(Course, Course.id == CourseContent.course_id)
        .where(
            CourseContent.id == content_id,
            CourseContent.course_id == course_id,
            Course.user_id == user_id,
        )
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    return row[0]


@router.get("/courses/{course_id}/media-assets", response_model=list[MediaAssetPublic])
async def list_media_assets(
    course_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[VideoAsset]:
    await _get_owned_course(db, course_id=course_id, user_id=current_user.id)

    res = await db.execute(
        select(VideoAsset)
        .where(VideoAsset.course_id == course_id)
        .order_by(VideoAsset.created_at.desc())
    )
    return list(res.scalars().all())


@router.post("/courses/{course_id}/media-assets", response_model=MediaAssetPublic)
async def create_media_asset(
    course_id: UUID,
    body: MediaAssetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> VideoAsset:
    await _get_owned_course(db, course_id=course_id, user_id=current_user.id)

    # Media assets rely on object storage keys (uploaded via presigned URLs).
    if not settings.s3_bucket:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="S3 is not configured (missing S3_BUCKET)",
        )

    file_key = (body.file_key or "").strip()
    if not file_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file_key is required")

    # Safety: prevent referencing keys outside the user's namespace.
    expected_prefix = f"users/{current_user.id}/"
    if not file_key.startswith(expected_prefix):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file_key")

    mt = (body.mime_type or "").strip()
    if mt and not mt.lower().startswith("video/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mime_type must be a video/* type")

    content_id: UUID | None = None
    if body.content_id:
        _ = await _validate_content_link(
            db, content_id=body.content_id, course_id=course_id, user_id=current_user.id
        )
        content_id = body.content_id

    asset = VideoAsset(
        course_id=course_id,
        content_id=content_id,
        provider="local",
        status="queued",
        source_file_key=file_key,
        original_filename=(body.original_filename or "").strip() or None,
        mime_type=mt or None,
        size_bytes=body.size_bytes,
        # Local uploads do not have a Bunny GUID.
        video_guid=None,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.get("/media-assets/{media_asset_id}", response_model=MediaAssetPublic)
async def get_media_asset(
    media_asset_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VideoAsset:
    res = await db.execute(
        select(VideoAsset, Course)
        .join(Course, Course.id == VideoAsset.course_id)
        .where(VideoAsset.id == media_asset_id, Course.user_id == current_user.id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")
    return row[0]


