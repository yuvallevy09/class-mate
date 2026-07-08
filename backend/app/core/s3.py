from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from app.core.settings import Settings


def s3_client(settings: Settings):
    kwargs: dict[str, Any] = {"service_name": "s3", "region_name": settings.s3_region}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
        # MinIO/local S3 often doesn't support virtual-host bucket addressing on localhost.
        kwargs["config"] = Config(s3={"addressing_style": "path"})
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client(**kwargs)
