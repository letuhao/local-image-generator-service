from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3
import structlog
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = structlog.get_logger(__name__)


class StorageError(Exception):
    """Terminal upload or read failure. Maps to arch §13 `storage_error` code."""


class StorageNotFoundError(StorageError):
    """S3 object does not exist. Separate from transport failures."""


# S3 error codes that are worth retrying. Permanent errors (AccessDenied,
# NoSuchBucket, InvalidAccessKeyId, etc.) are NOT here — retrying them burns
# 6+ seconds on a request that will never succeed.
_TRANSIENT_S3_CODES: frozenset[str] = frozenset(
    {
        "ServiceUnavailable",  # 503 S3 overload
        "SlowDown",  # 503 throttle
        "ThrottlingException",  # generic throttle
        "RequestTimeout",  # 408
        "RequestTimeoutException",
        "InternalError",  # 500 from S3
        "OperationAborted",  # transient conflict
    }
)


def _is_transient_client_error(exc: BaseException) -> bool:
    """Tenacity predicate — retry only on codes we know to be transient."""
    if not isinstance(exc, ClientError):
        return False
    code = exc.response.get("Error", {}).get("Code", "")
    return code in _TRANSIENT_S3_CODES


@dataclass(frozen=True, slots=True)
class S3Config:
    internal_endpoint: str  # e.g. http://minio:9000 (or real AWS URL)
    bucket: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"  # MinIO default; AWS overrides in prod

    @classmethod
    def from_env(cls) -> S3Config:
        return cls(
            internal_endpoint=os.environ.get("S3_INTERNAL_ENDPOINT", "http://minio:9000"),
            bucket=os.environ.get("S3_BUCKET", "image-gen"),
            access_key=os.environ.get("S3_ACCESS_KEY", ""),
            secret_key=os.environ.get("S3_SECRET_KEY", ""),
            region=os.environ.get("S3_REGION", "us-east-1"),
        )


def object_key_for(job_id: str, index: int, *, now: datetime | None = None) -> str:
    """Return `generations/YYYY/MM/DD/<job_id>/<index>.png`.

    Pure helper — no I/O. Testable without moto or boto3.
    """
    n = now or datetime.now(UTC)
    return f"generations/{n.year:04d}/{n.month:02d}/{n.day:02d}/{job_id}/{index}.png"


class S3Storage:
    """Single-client S3 wrapper. Arch v0.6 gateway model — no presign."""

    def __init__(self, cfg: S3Config) -> None:
        self._cfg = cfg
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=cfg.internal_endpoint,
            aws_access_key_id=cfg.access_key,
            aws_secret_access_key=cfg.secret_key,
            region_name=cfg.region,
            # MinIO requires path-style addressing; real AWS tolerates it.
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def ensure_bucket(self) -> None:
        """Create the configured bucket if absent. Idempotent. Raises StorageError on transport."""

        def _sync() -> None:
            try:
                self._client.head_bucket(Bucket=self._cfg.bucket)
                return
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("404", "NoSuchBucket", "NotFound"):
                    pass  # fall through to create
                else:
                    raise StorageError(f"head_bucket failed: {exc}") from exc
            try:
                self._client.create_bucket(Bucket=self._cfg.bucket)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    return  # race with another lifespan; fine
                raise StorageError(f"create_bucket failed: {exc}") from exc

        await asyncio.to_thread(_sync)
        log.info("s3.ensure_bucket.ok", bucket=self._cfg.bucket)

    async def upload_png(self, job_id: str, index: int, data: bytes) -> tuple[str, str]:
        """Upload PNG bytes to `object_key_for(job_id, index)`. Returns (bucket, key).

        Retries 3x with jittered exponential backoff on ClientError (arch §4.6).
        Terminal failure → StorageError.
        """
        key = object_key_for(job_id, index)

        def _sync() -> None:
            self._client.put_object(
                Bucket=self._cfg.bucket,
                Key=key,
                Body=data,
                ContentType="image/png",
            )

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=0.5, max=5.0),
                retry=retry_if_exception(_is_transient_client_error),
                reraise=True,
            ):
                with attempt:
                    await asyncio.to_thread(_sync)
        except ClientError as exc:
            raise StorageError(f"put_object failed after retries: {exc}") from exc

        log.info("s3.upload.ok", bucket=self._cfg.bucket, key=key, bytes=len(data))
        return self._cfg.bucket, key

    async def get_object(self, bucket: str, key: str) -> bytes:
        """Fetch object bytes. StorageNotFoundError on 404; StorageError on transport."""

        def _sync() -> bytes:
            try:
                resp = self._client.get_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    raise StorageNotFoundError(f"{bucket}/{key}") from exc
                raise StorageError(f"get_object failed: {exc}") from exc
            return resp["Body"].read()

        return await asyncio.to_thread(_sync)

    async def delete_object(self, bucket: str, key: str) -> None:
        """Delete an S3 object. 404 is silently tolerated (idempotent)."""

        def _sync() -> None:
            try:
                self._client.delete_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    return
                raise StorageError(f"delete_object failed: {exc}") from exc

        await asyncio.to_thread(_sync)
