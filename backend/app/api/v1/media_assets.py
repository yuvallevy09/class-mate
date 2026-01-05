from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.settings import Settings, get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.db.session import get_db
from app.schemas.media_asset import MediaAssetCreate, MediaAssetPublic
from app.schemas.transcript_segment import TranscriptSegmentPublic
from app.services.transcription import transcribe_media_asset

router = APIRouter(tags=["media-assets"])


def _s3_client(settings: Settings):
    import boto3

    kwargs: dict = {"service_name": "s3", "region_name": settings.s3_region}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client(**kwargs)


def _presign_thumbnail_url(settings: Settings, *, key: str) -> str:
    s3 = _s3_client(settings)
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=int(settings.s3_download_expires_seconds),
    )


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
    settings: Settings = Depends(get_settings),
) -> list[VideoAsset]:
    await _get_owned_course(db, course_id=course_id, user_id=current_user.id)

    res = await db.execute(
        select(VideoAsset)
        .where(VideoAsset.course_id == course_id)
        .order_by(VideoAsset.created_at.desc())
    )
    assets = list(res.scalars().all())
    out: list[MediaAssetPublic] = []
    for a in assets:
        # Build response model explicitly so we can include thumbnail_url.
        item = MediaAssetPublic.model_validate(a)
        if a.thumbnail_file_key and settings.s3_bucket:
            try:
                item.thumbnail_url = _presign_thumbnail_url(settings, key=a.thumbnail_file_key)
            except Exception:
                item.thumbnail_url = None
        out.append(item)
    return out


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
    settings: Settings = Depends(get_settings),
) -> VideoAsset:
    res = await db.execute(
        select(VideoAsset, Course)
        .join(Course, Course.id == VideoAsset.course_id)
        .where(VideoAsset.id == media_asset_id, Course.user_id == current_user.id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")
    a: VideoAsset = row[0]
    item = MediaAssetPublic.model_validate(a)
    if a.thumbnail_file_key and settings.s3_bucket:
        try:
            item.thumbnail_url = _presign_thumbnail_url(settings, key=a.thumbnail_file_key)
        except Exception:
            item.thumbnail_url = None
    return item


class StartTranscriptionRequest(BaseModel):
    language_code: str | None = None


class StartTranscriptionResponse(BaseModel):
    ok: bool = True
    media_asset_id: UUID
    status: str


@router.post("/media-assets/{media_asset_id}/transcribe", response_model=StartTranscriptionResponse)
async def start_transcription(
    media_asset_id: UUID,
    body: StartTranscriptionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> StartTranscriptionResponse:
    if not settings.runpod_api_key or not settings.runpod_endpoint_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Runpod is not configured (RUNPOD_API_KEY/RUNPOD_ENDPOINT_ID missing)",
        )

    res = await db.execute(
        select(VideoAsset, Course)
        .join(Course, Course.id == VideoAsset.course_id)
        .where(VideoAsset.id == media_asset_id, Course.user_id == current_user.id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")

    asset: VideoAsset = row[0]
    if asset.provider != "local":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only local media assets are supported")
    if not asset.source_file_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Media asset missing source_file_key")

    if asset.status in {"processing"}:
        return StartTranscriptionResponse(media_asset_id=asset.id, status=asset.status)

    # Mark as queued/processing and schedule background work.
    asset.status = "processing"
    asset.transcription_error = None
    asset.transcription_started_at = datetime.now(timezone.utc)
    await db.commit()

    background_tasks.add_task(
        transcribe_media_asset,
        media_asset_id=asset.id,
        requested_language=(body.language_code or None),
    )
    return StartTranscriptionResponse(media_asset_id=asset.id, status=asset.status)


@router.get("/media-assets/{media_asset_id}/segments", response_model=list[TranscriptSegmentPublic])
async def list_transcript_segments(
    media_asset_id: UUID,
    language_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TranscriptSegment]:
    # Ownership check via join.
    res = await db.execute(
        select(VideoAsset, Course)
        .join(Course, Course.id == VideoAsset.course_id)
        .where(VideoAsset.id == media_asset_id, Course.user_id == current_user.id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")
    asset: VideoAsset = row[0]

    stmt = select(TranscriptSegment).where(TranscriptSegment.video_asset_id == asset.id)
    if language_code:
        stmt = stmt.where(TranscriptSegment.language_code == language_code)
    stmt = stmt.order_by(TranscriptSegment.start_sec.asc())
    seg_res = await db.execute(stmt)
    return list(seg_res.scalars().all())


