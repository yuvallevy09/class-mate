from __future__ import annotations

import re
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.settings import Settings, get_settings
from app.db.models.course import Course
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter(prefix="/uploads", tags=["uploads"])


class PresignRequest(BaseModel):
    courseId: UUID
    filename: str
    contentType: str
    sizeBytes: int


class PresignResponse(BaseModel):
    key: str
    uploadUrl: str
    method: str = "PUT"
    expiresInSeconds: int


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_filename(name: str) -> str:
    # Strip paths and normalize whitespace/special chars.
    base = (name or "").split("/")[-1].split("\\")[-1].strip()
    base = _FILENAME_SAFE_RE.sub("_", base)
    base = base.strip("._-")
    if not base:
        return "file"
    # Avoid absurdly long keys.
    return base[:120]


def _s3_client(settings: Settings):
    kwargs: dict = {"service_name": "s3", "region_name": settings.s3_region}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
        # MinIO/local S3 often doesn't support virtual-host bucket addressing on localhost.
        kwargs["config"] = Config(s3={"addressing_style": "path"})
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client(**kwargs)


@router.post("/presign", response_model=PresignResponse)
async def presign_upload(
    body: PresignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> PresignResponse:
    if not settings.s3_bucket:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="S3 is not configured (missing S3_BUCKET)",
        )
    # Fail fast on the common misconfig: placeholder credentials + no endpoint_url.
    # Otherwise the browser gets a confusing 403 from AWS S3.
    if not settings.s3_endpoint_url:
        ak = (settings.s3_access_key_id or "").strip()
        sk = (settings.s3_secret_access_key or "").strip()
        if ak in {"access-id", "minioadmin", ""} or sk in {"access-key", "minioadmin", ""}:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=(
                    "S3 uploads are not configured for this environment. "
                    "For local dev with Docker MinIO, set: "
                    "S3_ENDPOINT_URL=http://localhost:9000, S3_BUCKET=classmate, "
                    "S3_ACCESS_KEY_ID=minioadmin, S3_SECRET_ACCESS_KEY=minioadmin."
                ),
            )

    # Ownership check: ensure user owns this course.
    res = await db.execute(
        select(Course).where(Course.id == body.courseId, Course.user_id == current_user.id)
    )
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    if body.sizeBytes < 0 or body.sizeBytes > settings.upload_max_size_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large")

    content_type = (body.contentType or "").strip() or "application/octet-stream"
    safe_name = _sanitize_filename(body.filename)
    key = f"users/{current_user.id}/courses/{course.id}/{uuid4()}_{safe_name}"

    s3 = _s3_client(settings)
    upload_url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.s3_bucket,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=int(settings.s3_presign_expires_seconds),
    )

    return PresignResponse(
        key=key,
        uploadUrl=upload_url,
        expiresInSeconds=int(settings.s3_presign_expires_seconds),
    )


