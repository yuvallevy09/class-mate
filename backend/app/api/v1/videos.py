from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.bunny.embed import derive_embed_url
from app.bunny.ingest import ingest_bunny_transcript_for_video_asset
from app.bunny.ingest import derive_captions_vtt_url
from app.core.settings import Settings, get_settings
from app.db.models.course import Course
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.db.session import get_db

router = APIRouter(tags=["videos"])


async def _ensure_owned_course(db: AsyncSession, *, course_id: UUID, user_id: int) -> Course:
    res = await db.execute(select(Course).where(Course.id == course_id, Course.user_id == user_id))
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


class BunnyUpsertVideoAssetRequest(BaseModel):
    videoLibraryId: int | None = Field(default=None, ge=1)
    pullZoneUrl: str = Field(min_length=1, max_length=255)
    captionsLanguageCode: str | None = Field(default=None, max_length=16)
    contentId: UUID | None = Field(default=None)


class VideoAssetPublic(BaseModel):
    id: UUID
    courseId: UUID
    contentId: UUID | None
    provider: str

    videoLibraryId: int | None
    videoGuid: str
    pullZoneUrl: str | None

    status: str
    lastWebhookStatus: int | None = None
    lastWebhookAt: str | None = None
    captionsLanguageCode: str | None
    captionsVttUrl: str | None
    captionsReadyAt: str | None = None
    transcriptIngestedAt: str | None

    availableLanguages: list[str] = Field(default_factory=list)

    embedUrl: str | None = None


def _asset_to_public(asset: VideoAsset) -> VideoAssetPublic:
    embed_url = None
    if asset.provider == "bunny" and asset.video_library_id and asset.video_guid:
        try:
            embed_url = derive_embed_url(video_library_id=int(asset.video_library_id), video_guid=asset.video_guid)
        except Exception:
            embed_url = None

    langs: list[str] = []
    if asset.captions_language_code and asset.captions_language_code.strip():
        langs.append(asset.captions_language_code.strip())

    return VideoAssetPublic(
        id=asset.id,
        courseId=asset.course_id,
        contentId=asset.content_id,
        provider=asset.provider,
        videoLibraryId=asset.video_library_id,
        videoGuid=asset.video_guid,
        pullZoneUrl=asset.pull_zone_url,
        status=asset.status,
        lastWebhookStatus=asset.last_webhook_status,
        lastWebhookAt=asset.last_webhook_at.isoformat() if asset.last_webhook_at else None,
        captionsLanguageCode=asset.captions_language_code,
        captionsVttUrl=asset.captions_vtt_url,
        captionsReadyAt=asset.captions_ready_at.isoformat() if asset.captions_ready_at else None,
        transcriptIngestedAt=asset.transcript_ingested_at.isoformat() if asset.transcript_ingested_at else None,
        availableLanguages=langs,
        embedUrl=embed_url,
    )


class VideoAssetPage(BaseModel):
    items: list[VideoAssetPublic]
    total: int
    limit: int
    offset: int


@router.get("/courses/{course_id}/videos", response_model=list[VideoAssetPublic])
async def list_video_assets(
    course_id: UUID,
    provider: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[VideoAssetPublic]:
    """
    List registered video assets for a course (paged). This is intended for UI wiring.
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="offset must be >= 0")

    stmt = select(VideoAsset).where(VideoAsset.course_id == course_id)
    if provider and provider.strip():
        stmt = stmt.where(VideoAsset.provider == provider.strip().lower())
    # Newest first.
    stmt = stmt.order_by(VideoAsset.created_at.desc()).offset(int(offset)).limit(int(limit))
    assets = (await db.execute(stmt)).scalars().all()
    return [_asset_to_public(a) for a in assets]


@router.get("/courses/{course_id}/videos/page", response_model=VideoAssetPage)
async def list_video_assets_page(
    course_id: UUID,
    provider: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VideoAssetPage:
    """
    Paged list endpoint returning a stable pagination contract:
      { items, total, limit, offset }
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="offset must be >= 0")

    provider_norm = provider.strip().lower() if provider and provider.strip() else None

    count_stmt = select(func.count()).select_from(VideoAsset).where(VideoAsset.course_id == course_id)
    if provider_norm:
        count_stmt = count_stmt.where(VideoAsset.provider == provider_norm)
    total = int((await db.execute(count_stmt)).scalar_one())

    stmt = select(VideoAsset).where(VideoAsset.course_id == course_id)
    if provider_norm:
        stmt = stmt.where(VideoAsset.provider == provider_norm)
    stmt = stmt.order_by(VideoAsset.created_at.desc()).offset(int(offset)).limit(int(limit))
    assets = (await db.execute(stmt)).scalars().all()

    return VideoAssetPage(
        items=[_asset_to_public(a) for a in assets],
        total=total,
        limit=int(limit),
        offset=int(offset),
    )


@router.get("/courses/{course_id}/videos/bunny/{video_guid}", response_model=VideoAssetPublic)
async def get_bunny_video_asset(
    course_id: UUID,
    video_guid: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VideoAssetPublic:
    """
    Fetch a single Bunny video asset for a course.
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)
    vg = (video_guid or "").strip()
    if not vg:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="video_guid is required")

    res = await db.execute(
        select(VideoAsset).where(
            VideoAsset.course_id == course_id,
            VideoAsset.provider == "bunny",
            VideoAsset.video_guid == vg,
        )
    )
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video asset not found")
    return _asset_to_public(asset)


@router.post("/courses/{course_id}/videos/bunny/{video_guid}/register", response_model=VideoAssetPublic)
async def register_bunny_video_asset_with_guid(
    course_id: UUID,
    video_guid: str,
    body: BunnyUpsertVideoAssetRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> VideoAssetPublic:
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    provider = "bunny"
    vg = (video_guid or "").strip()
    if not vg:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="video_guid is required")

    res = await db.execute(
        select(VideoAsset).where(
            VideoAsset.course_id == course_id,
            VideoAsset.provider == provider,
            VideoAsset.video_guid == vg,
        )
    )
    asset = res.scalar_one_or_none()

    lang = (body.captionsLanguageCode or settings.bunny_default_captions_language_code).strip() or "en"
    pull_zone = body.pullZoneUrl.strip()

    if asset is None:
        asset = VideoAsset(
            course_id=course_id,
            content_id=body.contentId,
            provider=provider,
            video_library_id=body.videoLibraryId,
            video_guid=vg,
            pull_zone_url=pull_zone,
            status="queued",
            captions_language_code=lang,
        )
        # Store derived VTT url for convenience/debugging (caller can override later if needed).
        try:
            asset.captions_vtt_url = derive_captions_vtt_url(
                pull_zone_url=pull_zone,
                video_guid=vg,
                language_code=lang,
            )
        except Exception:
            asset.captions_vtt_url = None

        db.add(asset)
        await db.commit()
        await db.refresh(asset)
        return _asset_to_public(asset)

    # Update editable fields.
    asset.video_library_id = body.videoLibraryId or asset.video_library_id
    asset.pull_zone_url = pull_zone or asset.pull_zone_url
    if body.contentId is not None:
        asset.content_id = body.contentId
    if body.captionsLanguageCode:
        asset.captions_language_code = body.captionsLanguageCode.strip()

    # Keep captions_vtt_url in sync if we can derive it.
    try:
        if asset.pull_zone_url and asset.captions_language_code:
            asset.captions_vtt_url = derive_captions_vtt_url(
                pull_zone_url=asset.pull_zone_url,
                video_guid=asset.video_guid,
                language_code=asset.captions_language_code,
            )
    except Exception:
        pass

    await db.commit()
    await db.refresh(asset)
    return _asset_to_public(asset)


class TranscriptSegmentPublic(BaseModel):
    id: UUID
    startSec: float
    endSec: float
    text: str
    languageCode: str


@router.get("/courses/{course_id}/videos/bunny/{video_guid}/segments", response_model=list[TranscriptSegmentPublic])
async def list_bunny_transcript_segments_debug(
    course_id: UUID,
    video_guid: str,
    language_code: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TranscriptSegmentPublic]:
    """
    Debug endpoint: list persisted transcript segments (no vector store involved).
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    vg = (video_guid or "").strip()
    if not vg:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="video_guid is required")

    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limit must be between 1 and 1000")
    if offset < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="offset must be >= 0")

    provider = "bunny"
    res = await db.execute(
        select(VideoAsset).where(
            VideoAsset.course_id == course_id,
            VideoAsset.provider == provider,
            VideoAsset.video_guid == vg,
        )
    )
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video asset not found")

    stmt = select(TranscriptSegment).where(TranscriptSegment.video_asset_id == asset.id)
    if language_code and language_code.strip():
        stmt = stmt.where(TranscriptSegment.language_code == language_code.strip())
    stmt = stmt.order_by(TranscriptSegment.start_sec.asc()).offset(int(offset)).limit(int(limit))
    segs = (await db.execute(stmt)).scalars().all()

    out: list[TranscriptSegmentPublic] = []
    for s in segs:
        out.append(
            TranscriptSegmentPublic(
                id=s.id,
                startSec=float(s.start_sec),
                endSec=float(s.end_sec),
                text=s.text,
                languageCode=s.language_code,
            )
        )
    return out


@router.get("/courses/{course_id}/videos/bunny/{video_guid}/embed")
async def get_bunny_embed_url(
    course_id: UUID,
    video_guid: str,
    t: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Helper endpoint: return an iframe embed URL, optionally with `t=` for timestamp jumps.
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    vg = (video_guid or "").strip()
    if not vg:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="video_guid is required")

    res = await db.execute(
        select(VideoAsset).where(
            VideoAsset.course_id == course_id,
            VideoAsset.provider == "bunny",
            VideoAsset.video_guid == vg,
        )
    )
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video asset not found")
    if not asset.video_library_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Video asset missing video_library_id")

    url = derive_embed_url(video_library_id=int(asset.video_library_id), video_guid=asset.video_guid, t=t)
    return {"url": url}


class ReingestRequest(BaseModel):
    languageCode: str | None = Field(default=None, max_length=16)


@router.post("/courses/{course_id}/videos/bunny/{video_guid}/transcript/reingest")
async def reingest_bunny_transcript(
    course_id: UUID,
    video_guid: str,
    body: ReingestRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Debug-friendly control: re-run ingestion for an EXISTING video asset.
    Does NOT overwrite metadata (pull zone, content_id, etc.).
    Optionally accepts a language code.
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    vg = (video_guid or "").strip()
    if not vg:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="video_guid is required")

    res = await db.execute(
        select(VideoAsset).where(
            VideoAsset.course_id == course_id,
            VideoAsset.provider == "bunny",
            VideoAsset.video_guid == vg,
        )
    )
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video asset not found")

    lang = (body.languageCode or "").strip() or None

    # If caller requests a language, set captions_language_code (but don't overwrite other metadata).
    if lang:
        asset.captions_language_code = lang

    # Explicit UI state.
    asset.status = "ingesting"
    await db.commit()

    background_tasks.add_task(
        ingest_bunny_transcript_for_video_asset,
        video_asset_id=asset.id,
        language_code=lang,
    )
    return {"ok": True}


@router.post("/courses/{course_id}/videos/bunny/{video_guid}/transcript/ingest")
async def ingest_bunny_transcript_debug(
    course_id: UUID,
    video_guid: str,
    body: BunnyUpsertVideoAssetRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Manual debug endpoint:
    - Upsert a Bunny video asset for the course (if missing)
    - Enqueue transcript ingestion (fetch VTT -> parse -> persist transcript_segments)
    """
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    provider = "bunny"
    vg = (video_guid or "").strip()
    if not vg:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="video_guid is required")

    res = await db.execute(
        select(VideoAsset).where(
            VideoAsset.course_id == course_id,
            VideoAsset.provider == provider,
            VideoAsset.video_guid == vg,
        )
    )
    asset = res.scalar_one_or_none()
    if asset is None:
        asset = VideoAsset(
            course_id=course_id,
            content_id=body.contentId,
            provider=provider,
            video_library_id=body.videoLibraryId,
            video_guid=vg,
            pull_zone_url=body.pullZoneUrl,
            status="queued",
            captions_language_code=(body.captionsLanguageCode or settings.bunny_default_captions_language_code).strip()
            or "en",
        )
        db.add(asset)
        await db.commit()
        await db.refresh(asset)
    else:
        # Update editable fields.
        asset.video_library_id = body.videoLibraryId or asset.video_library_id
        asset.pull_zone_url = body.pullZoneUrl or asset.pull_zone_url
        if body.contentId:
            asset.content_id = body.contentId
        if body.captionsLanguageCode:
            asset.captions_language_code = body.captionsLanguageCode.strip()
        await db.commit()

    background_tasks.add_task(ingest_bunny_transcript_for_video_asset, video_asset_id=asset.id)
    return {"ok": True, "videoAssetId": str(asset.id), "videoGuid": vg}


