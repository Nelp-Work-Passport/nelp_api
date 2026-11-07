"""File storage. Everything S3-specific stays behind this interface."""

from functools import lru_cache
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings
from app.errors import AppError


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def presigned_url(self, key: str, ttl_seconds: int) -> str:
        """A temporary link. It is signed locally, so it makes no network call."""


class S3Storage:
    """The bucket stays private. Every object is stored without public access."""

    def __init__(self, endpoint: str, region: str, bucket: str, access_key: str, secret_key: str):
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                # RumptyCloud has no DNS for bucket subdomains.
                s3={"addressing_style": "path"},
                # Newer boto3 checksums are an AWS default many S3 services reject.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                connect_timeout=5,
                read_timeout=20,
                retries={"max_attempts": 2},
            ),
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        except (BotoCoreError, ClientError) as exc:
            raise AppError(502, "STORAGE_ERROR", "The file could not be saved") from exc

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise AppError(502, "STORAGE_ERROR", "The file could not be removed") from exc

    def presigned_url(self, key: str, ttl_seconds: int) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.s3_bucket and settings.s3_access_key_id and settings.s3_secret_access_key)


@lru_cache
def _s3() -> S3Storage:
    settings = get_settings()
    return S3Storage(
        settings.s3_endpoint_url,
        settings.s3_region,
        settings.s3_bucket,
        settings.s3_access_key_id,
        settings.s3_secret_access_key,
    )


def get_storage() -> ObjectStorage:
    if not is_configured():
        raise AppError(503, "STORAGE_NOT_CONFIGURED", "File storage is not set up yet")
    return _s3()
