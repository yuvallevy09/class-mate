from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx
from sqlalchemy import delete, select

from app.bunny.vtt import merge_cues, parse_webvtt
from app.core.settings import Settings, get_settings
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.video_asset import VideoAsset
from app.db.session import get_session_maker


def _normalize_pull_zone(pull_zone_url: str) -> str:
    """
    Normalize pull zone identifier for Bunny URLs.
    Accepts:
    - "myzone"
    - "myzone.b-cdn.net"
    - "https://myzone.b-cdn.net"
    """
    s = (pull_zone_url or "").strip()
    if not s:
        return ""
    s = s.replace("https://", "").replace("http://", "")
    s = s.split("/")[0].strip()
    if s.endswith(".b-cdn.net"):
        s = s[: -len(".b-cdn.net")]
    return s.strip()


def derive_captions_vtt_url(*, pull_zone_url: str, video_guid: str, language_code: str) -> str:
    zone = _normalize_pull_zone(pull_zone_url)
    vg = (video_guid or "").strip()
    lang = (language_code or "").strip() or "en"
    if not zone or not vg:
        raise ValueError("Missing pull_zone_url or video_guid for captions URL derivation")
    return f"https://{zone}.b-cdn.net/{vg}/captions/{lang}.vtt"


async def _fetch_text(url: str, *, timeout_seconds: float = 15.0) -> str:
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        res = await client.get(url)
        res.raise_for_status()
        return res.text


async def ingest_bunny_transcript_for_video_asset(
    *,
    video_asset_id: UUID,
    language_code: Optional[str] = None,
) -> None:
    """
    Fetch Bunny WebVTT captions and upsert transcript segments for a video asset.
    Best-effort: raises exceptions to callers (BackgroundTasks will log).
    """
    settings: Settings = get_settings()
    SessionLocal = get_session_maker()

    async with SessionLocal() as db:
        res = await db.execute(select(VideoAsset).where(VideoAsset.id == video_asset_id))
        asset = res.scalar_one_or_none()
        if asset is None:
            return

        # Mark ingesting for clearer UI state (best-effort).
        asset.status = "ingesting"

        lang = (language_code or asset.captions_language_code or settings.bunny_default_captions_language_code).strip()
        if not lang:
            lang = "en"

        # Derive and persist captions URL (so we can debug later).
        if asset.captions_vtt_url and asset.captions_vtt_url.strip():
            vtt_url = asset.captions_vtt_url.strip()
        else:
            if not asset.pull_zone_url:
                raise ValueError("Video asset missing pull_zone_url")
            vtt_url = derive_captions_vtt_url(
                pull_zone_url=asset.pull_zone_url,
                video_guid=asset.video_guid,
                language_code=lang,
            )
            asset.captions_vtt_url = vtt_url

        try:
            # Fetch + parse.
            vtt_text = await _fetch_text(vtt_url)
            cues = parse_webvtt(vtt_text)
            merged = merge_cues(cues)
        except Exception:
            asset.status = "ingest_failed"
            await db.commit()
            raise

        # Upsert segments by "replace all for (asset, lang)".
        await db.execute(
            delete(TranscriptSegment).where(
                TranscriptSegment.video_asset_id == asset.id,
                TranscriptSegment.language_code == lang,
            )
        )
        for c in merged:
            db.add(
                TranscriptSegment(
                    course_id=asset.course_id,
                    video_asset_id=asset.id,
                    start_sec=float(c.start_sec),
                    end_sec=float(c.end_sec),
                    text=c.text,
                    language_code=lang,
                )
            )

        asset.captions_language_code = lang
        asset.transcript_ingested_at = datetime.now(timezone.utc)
        asset.status = "transcript_ingested"
        await db.commit()


