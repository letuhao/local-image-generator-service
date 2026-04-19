from __future__ import annotations

from datetime import UTC, datetime

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from app.storage.s3 import (
    S3Config,
    S3Storage,
    StorageError,
    StorageNotFoundError,
    object_key_for,
)


@pytest.fixture
def s3_config() -> S3Config:
    return S3Config(
        internal_endpoint="https://s3.us-east-1.amazonaws.com",
        bucket="image-gen-test",
        access_key="test-key",
        secret_key="test-secret",
    )


# ───────────────────────── pure helpers ─────────────────────────


def test_object_key_for_encodes_date_path() -> None:
    fixed = datetime(2026, 4, 19, 12, 34, 56, tzinfo=UTC)
    assert object_key_for("gen_abc", 0, now=fixed) == "generations/2026/04/19/gen_abc/0.png"


def test_object_key_for_uses_utc_when_now_is_none() -> None:
    # Smoke: just call it. Can't assert exact date without pinning.
    key = object_key_for("gen_xyz", 3)
    assert key.startswith("generations/")
    assert key.endswith("/gen_xyz/3.png")


def test_object_key_for_zero_pads_month_and_day() -> None:
    fixed = datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC)
    assert object_key_for("gen_abc", 0, now=fixed) == "generations/2026/01/05/gen_abc/0.png"


# ───────────────────────── round-trip with moto ─────────────────────────


async def test_ensure_bucket_creates_bucket(s3_config: S3Config) -> None:
    with mock_aws():
        store = S3Storage(s3_config)
        await store.ensure_bucket()
        client = boto3.client(
            "s3",
            aws_access_key_id=s3_config.access_key,
            aws_secret_access_key=s3_config.secret_key,
            region_name=s3_config.region,
        )
        assert client.head_bucket(Bucket=s3_config.bucket)


async def test_ensure_bucket_idempotent(s3_config: S3Config) -> None:
    """Second call to ensure_bucket must not error."""
    with mock_aws():
        store = S3Storage(s3_config)
        await store.ensure_bucket()
        await store.ensure_bucket()  # should be no-op


async def test_upload_png_round_trip(s3_config: S3Config) -> None:
    with mock_aws():
        store = S3Storage(s3_config)
        await store.ensure_bucket()

        png = b"\x89PNG\r\n\x1a\n" + b"hello"
        bucket, key = await store.upload_png("gen_roundtrip", 0, png)
        assert bucket == s3_config.bucket
        assert key.endswith("/gen_roundtrip/0.png")

        fetched = await store.get_object(bucket, key)
        assert fetched == png


async def test_get_object_missing_raises_not_found(s3_config: S3Config) -> None:
    with mock_aws():
        store = S3Storage(s3_config)
        await store.ensure_bucket()

        with pytest.raises(StorageNotFoundError):
            await store.get_object(s3_config.bucket, "does/not/exist.png")


async def test_upload_retries_transient_and_finally_fails(s3_config: S3Config) -> None:
    """ServiceUnavailable is transient → retries 3x before StorageError."""
    store = S3Storage(s3_config)

    class _AlwaysFailClient:
        def __init__(self) -> None:
            self.call_count = 0

        def put_object(self, **_kwargs):
            self.call_count += 1
            raise ClientError(
                error_response={"Error": {"Code": "ServiceUnavailable"}},
                operation_name="PutObject",
            )

    store._client = _AlwaysFailClient()  # type: ignore[assignment]
    with pytest.raises(StorageError):
        await store.upload_png("gen_fail", 0, b"\x89PNG\r\n\x1a\nbytes")
    assert store._client.call_count == 3  # type: ignore[attr-defined]


async def test_upload_does_not_retry_permanent_errors(s3_config: S3Config) -> None:
    """AccessDenied is permanent → fail IMMEDIATELY (1 call), not after 3 retries."""
    store = S3Storage(s3_config)

    class _AccessDeniedClient:
        def __init__(self) -> None:
            self.call_count = 0

        def put_object(self, **_kwargs):
            self.call_count += 1
            raise ClientError(
                error_response={"Error": {"Code": "AccessDenied"}},
                operation_name="PutObject",
            )

    store._client = _AccessDeniedClient()  # type: ignore[assignment]
    # AccessDenied is still wrapped in StorageError (arch §13 maps to storage_error),
    # but crucially the call count is 1, proving no retries.
    with pytest.raises(StorageError):
        await store.upload_png("gen_fail", 0, b"\x89PNG\r\n\x1a\nbytes")
    assert store._client.call_count == 1  # type: ignore[attr-defined]
