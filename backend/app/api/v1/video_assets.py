from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.settings import Settings, get_settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.db.session import get_db
from app.schemas.transcript_segment import TranscriptSegmentPublic
from app.schemas.video_asset import VideoAssetCreate, VideoAssetPublic
from app.services.transcription import transcribe_video_asset

router = APIRouter(tags=["video-assets"])

class TranscribeRequest(BaseModel):
    language_code: str | None = Field(default=None, max_length=16)
    force: bool = False


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


@router.get("/courses/{course_id}/video-assets", response_model=list[VideoAssetPublic])
async def list_video_assets(
    course_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[VideoAsset]:
    await _get_owned_course(db, course_id=course_id, user_id=current_user.id)
    res = await db.execute(
        select(VideoAsset).where(VideoAsset.course_id == course_id).order_by(VideoAsset.created_at.desc())
    )
    return list(res.scalars().all())


@router.post("/courses/{course_id}/video-assets", response_model=VideoAssetPublic)
async def create_video_asset(
    course_id: UUID,
    body: VideoAssetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> VideoAsset:
    await _get_owned_course(db, course_id=course_id, user_id=current_user.id)

    if not settings.s3_bucket:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="S3 is not configured (missing S3_BUCKET)",
        )

    file_key = (body.source_file_key or "").strip()
    if not file_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source_file_key is required")

    # Safety: prevent referencing keys outside the user's namespace.
    expected_prefix = f"users/{current_user.id}/"
    if not file_key.startswith(expected_prefix):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid source_file_key")

    mt = (body.mime_type or "").strip()
    if mt and not mt.lower().startswith("video/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mime_type must be a video/* type")

    content_id: UUID | None = None
    if body.content_id:
        _ = await _validate_content_link(
            db, content_id=body.content_id, course_id=course_id, user_id=current_user.id
        )
        content_id = body.content_id

    # Idempotency: if this source_file_key is already registered for the course, return it.
    existing_res = await db.execute(
        select(VideoAsset).where(VideoAsset.course_id == course_id, VideoAsset.source_file_key == file_key)
    )
    existing = existing_res.scalar_one_or_none()
    if existing is not None:
        return existing

    asset = VideoAsset(
        course_id=course_id,
        content_id=content_id,
        provider="local",
        status="uploaded",
        source_file_key=file_key,
        original_filename=(body.original_filename or "").strip() or None,
        mime_type=mt or None,
        size_bytes=body.size_bytes,
    )
    db.add(asset)
    try:
        await db.commit()
        await db.refresh(asset)
        return asset
    except IntegrityError:
        # Race-safe idempotency: another request may have inserted the same unique key.
        await db.rollback()
        existing_res = await db.execute(
            select(VideoAsset).where(VideoAsset.course_id == course_id, VideoAsset.source_file_key == file_key)
        )
        existing = existing_res.scalar_one_or_none()
        if existing is not None:
            return existing
        raise


@router.get("/video-assets/{video_asset_id}", response_model=VideoAssetPublic)
async def get_video_asset(
    video_asset_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VideoAsset:
    res = await db.execute(
        select(VideoAsset, Course)
        .join(Course, Course.id == VideoAsset.course_id)
        .where(VideoAsset.id == video_asset_id, Course.user_id == current_user.id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video asset not found")
    return row[0]


@router.get("/video-assets/{video_asset_id}/segments", response_model=list[TranscriptSegmentPublic])
async def list_video_asset_segments(
    video_asset_id: UUID,
    language_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TranscriptSegment]:
    # Ownership check via join.
    res = await db.execute(
        select(VideoAsset, Course)
        .join(Course, Course.id == VideoAsset.course_id)
        .where(VideoAsset.id == video_asset_id, Course.user_id == current_user.id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video asset not found")

    stmt = select(TranscriptSegment).where(TranscriptSegment.video_asset_id == video_asset_id)
    if language_code and language_code.strip():
        stmt = stmt.where(TranscriptSegment.language_code == language_code.strip())
    stmt = stmt.order_by(TranscriptSegment.start_sec.asc())
    seg_res = await db.execute(stmt)
    return list(seg_res.scalars().all())


@router.post("/video-assets/{video_asset_id}/transcribe")
async def start_transcription_stub(
    video_asset_id: UUID,
    background_tasks: BackgroundTasks,
    body: TranscribeRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    # Ownership check via join.
    res = await db.execute(
        select(VideoAsset, Course)
        .join(Course, Course.id == VideoAsset.course_id)
        .where(VideoAsset.id == video_asset_id, Course.user_id == current_user.id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video asset not found")

    asset: VideoAsset = row[0]

    # PR3.1: wire config validation now, implement the pipeline in PR3.2+.
    if not settings.s3_bucket:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="S3 is not configured (missing S3_BUCKET)",
        )
    if not (settings.runpod_api_key and settings.runpod_endpoint_id):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Runpod is not configured (missing RUNPOD_API_KEY / RUNPOD_ENDPOINT_ID)",
        )

    # PR3.2: update DB state and enqueue background work (actual pipeline comes in PR3.3+).
    requested_language = None
    force = False
    if body is not None:
        requested_language = (body.language_code or "").strip() or None
        force = bool(body.force)

    if asset.status in {"processing", "extracting_audio", "transcribing"}:
        return {"ok": True, "video_asset_id": str(asset.id), "status": asset.status}
    if asset.status == "done" and not force:
        return {"ok": True, "video_asset_id": str(asset.id), "status": asset.status}

    # Retry after error: clear completion markers so status reflects current run.
    if asset.status in {"error", "done"} and force:
        asset.transcription_job_id = None
        asset.transcription_completed_at = None
        asset.transcript_ingested_at = None

    # Stage-based progress: the worker will move extracting_audio -> transcribing -> done/error.
    asset.status = "extracting_audio"
    asset.transcription_error = None
    # Always reset started_at on force reruns; otherwise set if missing.
    now = datetime.now(timezone.utc)
    if force:
        asset.transcription_started_at = now
    else:
        asset.transcription_started_at = now if asset.transcription_started_at is None else asset.transcription_started_at
    await db.commit()

    background_tasks.add_task(
        transcribe_video_asset,
        video_asset_id=asset.id,
        requested_language=requested_language,
    )
    return {"ok": True, "video_asset_id": str(asset.id), "status": asset.status}


