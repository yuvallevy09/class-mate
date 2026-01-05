from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Iterable
from uuid import UUID

import boto3
import httpx
from sqlalchemy import delete, select

from app.core.settings import Settings, get_settings
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.video_asset import VideoAsset
from app.db.session import get_session_maker


@dataclass(frozen=True)
class Segment:
    start_sec: float
    end_sec: float
    text: str


def _s3_client(settings: Settings):
    kwargs: dict[str, Any] = {"service_name": "s3", "region_name": settings.s3_region}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client(**kwargs)


def _ffmpeg_extract_audio(*, ffmpeg_bin: str, video_path: Path, wav_path: Path) -> None:
    # Normalize to: mono, 16kHz, PCM WAV.
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        "-f",
        "wav",
        str(wav_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ffmpeg_extract_thumbnail(
    *,
    ffmpeg_bin: str,
    video_path: Path,
    thumbnail_path: Path,
    seek_seconds: float = 1.0,
) -> None:
    # Best-effort thumbnail extraction: seek a bit into the video and capture one frame.
    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss",
        str(seek_seconds),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=640:-2",
        "-q:v",
        "3",
        str(thumbnail_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class RunpodClient:
    """Minimal Runpod serverless client (submit + poll).

    Runpod's serverless endpoints commonly follow:
      POST https://api.runpod.ai/v2/{endpoint_id}/run
      POST https://api.runpod.ai/v2/{endpoint_id}/runsync
      GET  https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}

    We keep parsing tolerant so minor schema changes won't break everything.
    """

    def __init__(self, *, api_key: str, endpoint_id: str, timeout: float = 60.0, use_runsync: bool = True):
        self._api_key = api_key
        self._endpoint_id = endpoint_id
        self._timeout = timeout
        self._use_runsync = use_runsync

    @property
    def _base(self) -> str:
        return f"https://api.runpod.ai/v2/{self._endpoint_id}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    async def submit_audio_url(
        self,
        *,
        audio_url: str,
        language: str | None = None,
        model: str | None = None,
        extra_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Standard Runpod contract: {"input": {...}}.
        # The standard Runpod faster-whisper worker expects a URL under `audio_url`.
        input_payload: dict[str, Any] = {"audio_url": audio_url}
        if language:
            input_payload["language"] = language
        if model:
            input_payload["model"] = model
        if extra_input:
            input_payload.update(extra_input)

        path = "runsync" if self._use_runsync else "run"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            res = await client.post(f"{self._base}/{path}", headers=self._headers(), json={"input": input_payload})
            res.raise_for_status()
            return res.json()

    @staticmethod
    def extract_job_id(payload: dict[str, Any]) -> str:
        job_id = payload.get("id") or payload.get("jobId") or payload.get("job_id")
        if not job_id:
            raise RuntimeError("Runpod response missing job id")
        return str(job_id)

    async def poll_until_complete(
        self,
        *,
        job_id: str,
        poll_interval_seconds: float,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            while True:
                res = await client.get(f"{self._base}/status/{job_id}", headers=self._headers())
                res.raise_for_status()
                data = res.json()

                status = str(data.get("status") or "").lower()
                if status in {"completed", "complete", "succeeded", "success"}:
                    return data
                if status in {"failed", "error", "cancelled", "canceled"}:
                    return data
                if time.time() >= deadline:
                    raise TimeoutError("Runpod job timed out")
                await asyncio.sleep(poll_interval_seconds)


def _parse_segments_from_runpod_output(payload: dict[str, Any]) -> tuple[str, list[Segment]]:
    # Expect something like: {"output": {"segments": [...], "language": "en"}}.
    output = payload.get("output") or payload.get("result") or {}
    if isinstance(output, dict):
        language = output.get("language") or "und"
        segs = output.get("segments") or output.get("transcript") or []
    else:
        language = "und"
        segs = []

    segments: list[Segment] = []
    if isinstance(segs, Iterable):
        for s in segs:
            if not isinstance(s, dict):
                continue
            start = s.get("start") if "start" in s else s.get("start_sec")
            end = s.get("end") if "end" in s else s.get("end_sec")
            text = s.get("text") or s.get("segment") or ""
            if start is None or end is None:
                continue
            try:
                segments.append(Segment(start_sec=float(start), end_sec=float(end), text=str(text)))
            except Exception:
                continue
    return str(language), segments


def _runpod_status_output_error(payload: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
    """Normalize Runpod responses from /run, /runsync, or /status/{id}."""
    status = str(payload.get("status") or "").lower()
    output_raw = payload.get("output") or payload.get("result") or {}
    output = output_raw if isinstance(output_raw, dict) else {}
    err: Any = payload.get("error")
    if isinstance(err, dict):
        err = err.get("message") or err.get("detail") or str(err)
    if err is not None:
        err = str(err)
    return status, output, err


def _presign_get_object_url(settings: Settings, *, key: str, expires_seconds: int) -> str:
    s3 = _s3_client(settings)
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=int(expires_seconds),
    )


async def transcribe_media_asset(*, media_asset_id: UUID, requested_language: str | None = None) -> None:
    """Background task: download video -> ffmpeg -> Runpod -> persist transcript_segments.

    Updates `video_assets.status` and transcription_* fields as it progresses.
    """

    settings = get_settings()
    if not settings.s3_bucket:
        raise RuntimeError("S3_BUCKET missing")
    if not settings.runpod_api_key or not settings.runpod_endpoint_id:
        raise RuntimeError("Runpod is not configured (RUNPOD_API_KEY/RUNPOD_ENDPOINT_ID missing)")

    SessionLocal = get_session_maker()

    async with SessionLocal() as db:
        res = await db.execute(select(VideoAsset).where(VideoAsset.id == media_asset_id))
        asset = res.scalar_one_or_none()
        if asset is None:
            return
        if not asset.source_file_key:
            asset.status = "error"
            asset.transcription_error = "Missing source_file_key"
            asset.transcription_completed_at = datetime.now(timezone.utc)
            await db.commit()
            return

        # Mark processing.
        asset.status = "processing"
        asset.transcription_error = None
        asset.transcription_started_at = datetime.now(timezone.utc)
        await db.commit()

        s3 = _s3_client(settings)
        runpod = RunpodClient(
            api_key=settings.runpod_api_key,
            endpoint_id=settings.runpod_endpoint_id,
            use_runsync=bool(settings.runpod_use_runsync),
        )

        try:
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                video_path = td_path / "input.video"
                wav_path = td_path / "audio.wav"
                thumb_path = td_path / "thumbnail.jpg"

                # Download video from S3.
                def _download():
                    obj = s3.get_object(Bucket=settings.s3_bucket, Key=asset.source_file_key)
                    body = obj["Body"].read()
                    video_path.write_bytes(body)

                await asyncio.to_thread(_download)

                # Best-effort thumbnail generation (do not fail the whole job).
                try:
                    await asyncio.to_thread(
                        _ffmpeg_extract_thumbnail,
                        ffmpeg_bin=settings.ffmpeg_bin,
                        video_path=video_path,
                        thumbnail_path=thumb_path,
                        seek_seconds=float(settings.thumbnail_seek_seconds),
                    )
                    if thumb_path.exists() and thumb_path.stat().st_size > 0:
                        thumb_key = (
                            asset.thumbnail_file_key
                            or f"courses/{asset.course_id}/media-assets/{asset.id}/thumbnail.jpg"
                        )

                        def _upload_thumb():
                            s3.put_object(
                                Bucket=settings.s3_bucket,
                                Key=thumb_key,
                                Body=thumb_path.read_bytes(),
                                ContentType="image/jpeg",
                            )

                        await asyncio.to_thread(_upload_thumb)
                        asset.thumbnail_file_key = thumb_key
                        asset.thumbnail_mime_type = "image/jpeg"
                        asset.thumbnail_generated_at = datetime.now(timezone.utc)
                        await db.commit()
                except Exception:
                    # Ignore thumbnail failures; transcription can still succeed.
                    pass

                # Extract audio.
                await asyncio.to_thread(
                    _ffmpeg_extract_audio,
                    ffmpeg_bin=settings.ffmpeg_bin,
                    video_path=video_path,
                    wav_path=wav_path,
                )

                # Upload audio to S3 so Runpod can fetch it.
                audio_key = asset.audio_file_key or f"courses/{asset.course_id}/media-assets/{asset.id}/audio.wav"

                def _upload_audio():
                    s3.put_object(
                        Bucket=settings.s3_bucket,
                        Key=audio_key,
                        Body=wav_path.read_bytes(),
                        ContentType="audio/wav",
                    )

                await asyncio.to_thread(_upload_audio)
                asset.audio_file_key = audio_key
                await db.commit()

                # Presign audio for Runpod (must be reachable from Runpod over HTTPS).
                audio_url = _presign_get_object_url(
                    settings,
                    key=audio_key,
                    expires_seconds=int(settings.s3_audio_presign_expires_seconds),
                )

                # Call Runpod.
                result = await runpod.submit_audio_url(
                    audio_url=audio_url,
                    language=requested_language,
                    model=settings.runpod_whisper_model,
                )

                # If we used /run, the initial response is queued; poll until complete.
                if not settings.runpod_use_runsync:
                    job_id = runpod.extract_job_id(result)
                    asset.transcription_job_id = job_id
                    await db.commit()
                    result = await runpod.poll_until_complete(
                        job_id=job_id,
                        poll_interval_seconds=float(settings.runpod_poll_interval_seconds),
                        timeout_seconds=float(settings.runpod_timeout_seconds),
                    )
                else:
                    # runsync typically returns an id; store it if present for debugging.
                    try:
                        asset.transcription_job_id = runpod.extract_job_id(result)
                        await db.commit()
                    except Exception:
                        pass

                status, output, err = _runpod_status_output_error(result)
                success_statuses = {"completed", "complete", "succeeded", "success"}
                if status in {"failed", "error", "cancelled", "canceled"} or err or status not in success_statuses:
                    asset.status = "error"
                    asset.transcription_error = err or f"Runpod job did not complete successfully (status={status})"
                    asset.transcription_completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    return

                language_code, segments = _parse_segments_from_runpod_output({"output": output})
                if requested_language:
                    language_code = requested_language

                # Replace-all segments for (video_asset_id, language_code).
                await db.execute(
                    delete(TranscriptSegment).where(
                        TranscriptSegment.video_asset_id == asset.id,
                        TranscriptSegment.language_code == language_code,
                    )
                )
                for seg in segments:
                    db.add(
                        TranscriptSegment(
                            course_id=asset.course_id,
                            video_asset_id=asset.id,
                            start_sec=seg.start_sec,
                            end_sec=seg.end_sec,
                            text=seg.text,
                            language_code=language_code,
                        )
                    )
                asset.status = "done"
                asset.transcript_ingested_at = datetime.now(timezone.utc)
                asset.transcription_completed_at = datetime.now(timezone.utc)
                await db.commit()
        except subprocess.CalledProcessError:
            asset.status = "error"
            asset.transcription_error = "ffmpeg failed"
            asset.transcription_completed_at = datetime.now(timezone.utc)
            await db.commit()
        except TimeoutError as e:
            asset.status = "error"
            asset.transcription_error = str(e)
            asset.transcription_completed_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception as e:
            asset.status = "error"
            asset.transcription_error = str(e)
            asset.transcription_completed_at = datetime.now(timezone.utc)
            await db.commit()


