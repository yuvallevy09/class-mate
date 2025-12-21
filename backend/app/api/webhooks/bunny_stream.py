from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bunny.ingest import ingest_bunny_transcript_for_video_asset
from app.core.rate_limit import FixedWindowRateLimiter
from app.core.settings import Settings, get_settings
from app.db.models.video_asset import VideoAsset
from app.db.session import get_db

router = APIRouter(prefix="/api/webhooks/bunny", tags=["webhooks"])


# Bunny Stream webhook status codes (per Bunny docs).
STATUS_FINISHED = 3
STATUS_CAPTIONS_GENERATED = 9


class BunnyStreamWebhookPayload(BaseModel):
    VideoLibraryId: int = Field(..., ge=1)
    VideoGuid: str = Field(..., min_length=1, max_length=128)
    Status: int = Field(..., ge=0)


_limiter = FixedWindowRateLimiter()


def _client_ip(request: Request) -> str:
    # MVP: honor X-Forwarded-For if present (first IP), else use request.client.
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return (getattr(request.client, "host", None) or "unknown").strip()


@router.post("/stream/{secret}")
async def bunny_stream_webhook(
    secret: str,
    payload: BunnyStreamWebhookPayload,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    CSRF-exempt webhook handler for Bunny Stream.

    Fast path only:
    - Validate secret
    - Validate payload schema (Pydantic)
    - Update video_assets status (idempotent)
    - Enqueue transcript ingestion on CaptionsGenerated
    """
    expected = (settings.bunny_webhook_secret or "").strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Bunny webhook is not configured")
    if secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret")

    # Basic best-effort rate limit (per-IP, per-process).
    ip = _client_ip(request)
    rl = await _limiter.hit(
        key=f"bunny_stream:{ip}",
        limit=int(settings.bunny_webhook_rate_limit_per_minute),
        window_seconds=60,
    )
    if not rl.allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

    video_guid = payload.VideoGuid.strip()
    provider = "bunny"

    # Prefer matching by (provider, guid, library_id) to avoid ambiguity, but fall back to guid-only.
    res = await db.execute(
        select(VideoAsset).where(
            VideoAsset.provider == provider,
            VideoAsset.video_guid == video_guid,
            VideoAsset.video_library_id == int(payload.VideoLibraryId),
        )
    )
    assets = list(res.scalars().all())
    if not assets:
        res = await db.execute(
            select(VideoAsset).where(
                VideoAsset.provider == provider,
                VideoAsset.video_guid == video_guid,
            )
        )
        assets = list(res.scalars().all())

    asset = assets[0] if assets else None
    if asset is None:
        # Idempotent + non-fatal: asset might not exist yet if user hasn't registered it.
        return {"ok": True, "ignored": True, "reason": "video asset not found"}

    now = datetime.now(timezone.utc)
    asset.video_library_id = int(payload.VideoLibraryId)
    asset.last_webhook_status = int(payload.Status)
    asset.last_webhook_at = now

    if int(payload.Status) == STATUS_FINISHED:
        asset.status = "finished"
    elif int(payload.Status) == STATUS_CAPTIONS_GENERATED:
        # Match Bunny naming ("CaptionsGenerated") for UI clarity.
        asset.status = "captions_generated"
        asset.captions_ready_at = now
        # Defer the network work (fetch+parse VTT). The worker sets status transitions.
        background_tasks.add_task(ingest_bunny_transcript_for_video_asset, video_asset_id=UUID(str(asset.id)))

    await db.commit()
    return {"ok": True}


