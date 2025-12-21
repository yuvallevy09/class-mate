from __future__ import annotations

from urllib.parse import urlencode


def derive_embed_url(
    *,
    video_library_id: int,
    video_guid: str,
    t: str | int | float | None = None,
    captions: str | None = None,
) -> str:
    """
    Build a Bunny Stream embed URL for an iframe.

    Base pattern (per Bunny docs):
      https://iframe.mediadelivery.net/embed/{video_library_id}/{video_id}

    Supports optional query params like:
    - t: start time (seconds, hh:mm:ss, or 1h20m45s)
    - captions: caption short-code string
    """
    vg = (video_guid or "").strip()
    if not vg:
        raise ValueError("video_guid is required")
    if int(video_library_id) <= 0:
        raise ValueError("video_library_id must be > 0")

    base = f"https://iframe.mediadelivery.net/embed/{int(video_library_id)}/{vg}"
    params: dict[str, str] = {}
    if t is not None and str(t).strip():
        params["t"] = str(t).strip()
    if captions is not None and str(captions).strip():
        params["captions"] = str(captions).strip()

    if not params:
        return base
    return f"{base}?{urlencode(params)}"


