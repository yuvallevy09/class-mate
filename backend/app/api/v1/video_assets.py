from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
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
from app.schemas.transcript_segment import TranscriptSegmentPublic
from app.schemas.video_asset import VideoAssetCreate, VideoAssetPublic

router = APIRouter(tags=["video-assets"])


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
    await db.commit()
    await db.refresh(asset)
    return asset


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

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Transcription is not implemented yet (PR3 will add ffmpeg + Runpod + whisper-timestamped).",
    )


