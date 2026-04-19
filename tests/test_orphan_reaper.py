from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.queue.jobs import create_queued, set_completed, set_fetched, set_running
from app.queue.reaper import OrphanReaper
from app.queue.store import JobStore


class _FakeS3:
    def __init__(self) -> None:
        self.bucket = "image-gen-test"
        self.objects: set[tuple[str, str]] = set()
        self.delete_calls: list[tuple[str, str]] = []

    def add(self, bucket: str, key: str) -> None:
        self.objects.add((bucket, key))

    async def delete_object(self, bucket: str, key: str) -> None:
        self.delete_calls.append((bucket, key))
        self.objects.discard((bucket, key))


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[JobStore]:
    s = JobStore(str(tmp_path / "jobs.db"))
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


async def _backdate(store: JobStore, job_id: str, *, days_ago: int) -> None:
    """Rewind updated_at to simulate a stale completed job."""
    stamp = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    async with store.write() as conn:
        await conn.execute(
            "UPDATE jobs SET updated_at=? WHERE id=?",
            (stamp, job_id),
        )


async def test_reaper_deletes_unfetched_completed_job(store: JobStore) -> None:
    s3 = _FakeS3()
    job = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, job.id, prompt_id="pid", client_id="cid")
    bucket, key = "image-gen-test", f"generations/test/{job.id}/0.png"
    s3.add(bucket, key)
    await set_completed(store, job.id, output_keys=[f"{bucket}/{key}"], result_json="{}")
    await _backdate(store, job.id, days_ago=2)

    reaper = OrphanReaper(store=store, s3=s3, ttl_seconds=86400)
    deleted = await reaper.reap_once()
    assert deleted == 1
    assert (bucket, key) not in s3.objects


async def test_reaper_skips_fetched_jobs(store: JobStore) -> None:
    """If fetched_at is set, the reaper must NOT delete the S3 object."""
    s3 = _FakeS3()
    job = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, job.id, prompt_id="pid", client_id="cid")
    bucket, key = "image-gen-test", f"generations/test/{job.id}/0.png"
    s3.add(bucket, key)
    await set_completed(store, job.id, output_keys=[f"{bucket}/{key}"], result_json="{}")
    await set_fetched(store, job.id)
    await _backdate(store, job.id, days_ago=2)

    reaper = OrphanReaper(store=store, s3=s3, ttl_seconds=86400)
    deleted = await reaper.reap_once()
    assert deleted == 0
    assert (bucket, key) in s3.objects


async def test_reaper_skips_running_jobs(store: JobStore) -> None:
    """Non-completed rows are never reaped, regardless of age."""
    s3 = _FakeS3()
    job = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, job.id, prompt_id="pid", client_id="cid")
    await _backdate(store, job.id, days_ago=30)

    reaper = OrphanReaper(store=store, s3=s3, ttl_seconds=86400)
    deleted = await reaper.reap_once()
    assert deleted == 0


async def test_reaper_respects_ttl_boundary(store: JobStore) -> None:
    """Recently-completed (< TTL) should not be reaped, even if unfetched."""
    s3 = _FakeS3()
    job = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, job.id, prompt_id="pid", client_id="cid")
    bucket, key = "image-gen-test", f"generations/test/{job.id}/0.png"
    s3.add(bucket, key)
    await set_completed(store, job.id, output_keys=[f"{bucket}/{key}"], result_json="{}")
    # No backdate — updated_at is now.

    reaper = OrphanReaper(store=store, s3=s3, ttl_seconds=86400)
    deleted = await reaper.reap_once()
    assert deleted == 0
    assert (bucket, key) in s3.objects
