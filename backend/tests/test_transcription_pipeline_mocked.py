from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.course import Course
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.services import transcription as svc


async def _can_connect(database_url: str) -> bool:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


def _run_migrations_sync() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


async def _create_user(database_url: str, *, email: str, password: str) -> User:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            user = User(email=email, hashed_password=hash_password(password))
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user
    finally:
        await engine.dispose()


async def _create_course(database_url: str, *, user_id: int, name: str) -> Course:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            course = Course(user_id=user_id, name=name, description=None)
            session.add(course)
            await session.commit()
            await session.refresh(course)
            return course
    finally:
        await engine.dispose()


async def _create_media_asset(database_url: str, *, course_id, source_key: str) -> VideoAsset:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            asset = VideoAsset(
                course_id=course_id,
                provider="local",
                status="queued",
                source_file_key=source_key,
                mime_type="video/mp4",
                original_filename="video.mp4",
                size_bytes=10,
                video_guid=None,
            )
            session.add(asset)
            await session.commit()
            await session.refresh(asset)
            return asset
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transcription_pipeline_persists_segments(monkeypatch) -> None:
    # Configure settings for the service.
    monkeypatch.setenv("S3_BUCKET", "classmate")
    monkeypatch.setenv("RUNPOD_API_KEY", "test")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint")
    get_settings.cache_clear()
    settings = get_settings()

    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")

    await asyncio.to_thread(_run_migrations_sync)

    user = await _create_user(settings.database_url, email=f"u-{uuid4()}@e.com", password="pw")
    course = await _create_course(settings.database_url, user_id=user.id, name="Course")
    asset = await _create_media_asset(
        settings.database_url, course_id=course.id, source_key=f"users/{user.id}/courses/{course.id}/x.mp4"
    )

    # Stub S3:
    # - download video: return some bytes (content doesn't matter because ffmpeg is stubbed)
    # - upload extracted audio: capture the key
    # - generate presigned URL for audio: return a stable HTTPS URL
    class _Body:
        def read(self):
            return b"fake-video"

    class _StubS3:
        def __init__(self):
            self.put_calls = []

        def get_object(self, *, Bucket, Key):
            assert Bucket == settings.s3_bucket
            assert Key == asset.source_file_key
            return {"Body": _Body()}

        def put_object(self, *, Bucket, Key, Body, ContentType):
            assert Bucket == settings.s3_bucket
            assert Key
            assert Body  # bytes
            assert ContentType in {"audio/wav", "image/jpeg"}
            self.put_calls.append({"Bucket": Bucket, "Key": Key})

        def generate_presigned_url(self, *, ClientMethod, Params, ExpiresIn):
            assert ClientMethod == "get_object"
            assert Params["Bucket"] == settings.s3_bucket
            assert Params["Key"]
            assert ExpiresIn
            # Must be HTTPS-reachable by Runpod in the real system.
            return f"https://public-s3.example.test/{Params['Bucket']}/{Params['Key']}"

    stub_s3 = _StubS3()
    monkeypatch.setattr(svc, "_s3_client", lambda _settings: stub_s3)

    # Stub ffmpeg extraction: just write a dummy wav file.
    def _fake_ffmpeg_extract_audio(*, ffmpeg_bin: str, video_path: Path, wav_path: Path) -> None:
        wav_path.write_bytes(b"RIFF....WAVEfmt ")  # not a real wav, but enough for test

    monkeypatch.setattr(svc, "_ffmpeg_extract_audio", _fake_ffmpeg_extract_audio)

    # Stub Runpod client: expect an audio URL and return a completed result with segments.
    class _StubRunpod:
        def __init__(self, *args, **kwargs):
            pass

        async def submit_audio_url(
            self,
            *,
            audio_url: str,
            language: str | None = None,
            model: str | None = None,
            extra_input: dict | None = None,
        ):
            assert audio_url.startswith("https://")
            assert model  # default comes from settings
            assert extra_input is None
            return {
                "id": "job-1",
                "status": "COMPLETED",
                "output": {
                    "language": "en",
                    "segments": [
                        {"start": 0.0, "end": 1.2, "text": "hello"},
                        {"start": 1.2, "end": 2.0, "text": "world"},
                    ],
                },
            }

    monkeypatch.setattr(svc, "RunpodClient", _StubRunpod)

    # Run the background task function directly.
    await svc.transcribe_media_asset(media_asset_id=asset.id, requested_language=None)

    # Verify DB state.
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            a = (await session.execute(select(VideoAsset).where(VideoAsset.id == asset.id))).scalar_one()
            assert a.status == "done"
            assert a.transcription_job_id == "job-1"
            assert a.audio_file_key is not None
            assert a.transcript_ingested_at is not None
            segs = (
                await session.execute(
                    select(TranscriptSegment)
                    .where(TranscriptSegment.video_asset_id == asset.id)
                    .order_by(TranscriptSegment.start_sec.asc())
                )
            ).scalars().all()
            assert [s.text for s in segs] == ["hello", "world"]
            assert segs[0].language_code == "en"
    finally:
        await engine.dispose()


