from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.backends.base import (
    ComfyNodeError,
    ComfyTimeoutError,
    ComfyUnreachableError,
    ModelConfig,
)
from app.queue.jobs import create_queued, get_by_id
from app.queue.store import JobStore
from app.queue.worker import JobResult, QueueWorker
from app.registry.models import Registry


class _FakeAdapter:
    def __init__(self) -> None:
        self.client_id = "fake-client"
        self.images: list[bytes] = [b"\x89PNG\r\n\x1a\n" + b"payload"]
        self.submit_exc: Exception | None = None
        self.wait_exc: Exception | None = None
        self.fetch_exc: Exception | None = None
        self.submits: list[dict] = []

    async def submit(self, graph: dict) -> str:
        if self.submit_exc:
            raise self.submit_exc
        self.submits.append(graph)
        return f"pid-{len(self.submits)}"

    async def wait_for_completion(self, prompt_id: str, timeout_s: float) -> None:
        if self.wait_exc:
            raise self.wait_exc

    async def fetch_outputs(self, prompt_id: str) -> list[bytes]:
        if self.fetch_exc:
            raise self.fetch_exc
        return list(self.images)

    async def close(self) -> None:  # pragma: no cover
        pass


class _FakeS3:
    def __init__(self) -> None:
        self.bucket = "image-gen-test"
        self.uploads: list[tuple[str, str]] = []

    async def upload_png(self, job_id: str, index: int, data: bytes) -> tuple[str, str]:
        key = f"generations/test/{job_id}/{index}.png"
        self.uploads.append((self.bucket, key))
        return self.bucket, key

    async def get_object(self, bucket: str, key: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    async def ensure_bucket(self) -> None:  # pragma: no cover
        pass


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[JobStore]:
    s = JobStore(str(tmp_path / "jobs.db"))
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def registry() -> Registry:
    cfg = ModelConfig(
        name="noobai-xl-v1.1",
        backend="comfyui",
        workflow_path="workflows/sdxl_eps.json",
        checkpoint="checkpoints/NoobAI-XL-v1.1.safetensors",
        vae="vae/sdxl_vae.safetensors",
        vram_estimate_gb=7.0,
        prediction="eps",
        capabilities={"image_gen": True},
        defaults={
            "size": "1024x1024",
            "steps": 28,
            "cfg": 5.0,
            "sampler": "euler_ancestral",
            "scheduler": "karras",
            "negative_prompt": "worst quality, low quality",
        },
        limits={"steps_max": 60, "n_max": 4, "size_max_pixels": 1572864},
    )
    return Registry({cfg.name: cfg})


@pytest.fixture
async def worker(
    store: JobStore, registry: Registry, tmp_path: Path
) -> AsyncIterator[tuple[QueueWorker, _FakeAdapter, _FakeS3, asyncio.Task]]:
    adapter = _FakeAdapter()
    s3 = _FakeS3()
    w = QueueWorker(
        store=store,
        adapter=adapter,
        s3=s3,
        registry=registry,
        public_base_url="http://testserver",
        job_timeout_s=30.0,
        max_queue=20,
        loras_root=tmp_path,
    )
    task = asyncio.create_task(w.run(), name="queue-worker-test")
    try:
        yield w, adapter, s3, task
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _input_json(**overrides: object) -> str:
    body = {"model": "noobai-xl-v1.1", "prompt": "a cat", "size": "512x512", "steps": 1}
    body.update(overrides)
    return json.dumps(body)


async def test_worker_processes_single_job(
    worker: tuple[QueueWorker, _FakeAdapter, _FakeS3, asyncio.Task],
    store: JobStore,
) -> None:
    w, _adapter, s3, _task = worker
    job = await create_queued(store, model_name="noobai-xl-v1.1", input_json=_input_json())
    fut = await w.enqueue(job)
    assert fut is not None
    result = await asyncio.wait_for(fut, timeout=5.0)
    assert isinstance(result, JobResult)
    assert len(result.data) == 1
    assert result.data[0]["url"].endswith("/0.png")
    # DB state
    row = await get_by_id(store, job.id)
    assert row is not None
    assert row.status == "completed"
    assert len(row.output_keys) == 1
    # S3 upload happened
    assert len(s3.uploads) == 1


async def test_worker_serialises_three_concurrent_jobs(
    worker: tuple[QueueWorker, _FakeAdapter, _FakeS3, asyncio.Task],
    store: JobStore,
) -> None:
    w, adapter, _s3, _task = worker
    jobs = []
    futures = []
    for _ in range(3):
        j = await create_queued(store, model_name="noobai-xl-v1.1", input_json=_input_json())
        jobs.append(j)
        fut = await w.enqueue(j)
        assert fut is not None
        futures.append(fut)

    results = await asyncio.gather(*futures)
    assert len(results) == 3
    # Worker called adapter.submit exactly 3 times, in order (serial).
    assert len(adapter.submits) == 3


async def test_worker_handles_comfy_node_error(
    worker: tuple[QueueWorker, _FakeAdapter, _FakeS3, asyncio.Task],
    store: JobStore,
) -> None:
    w, adapter, _s3, _task = worker
    adapter.submit_exc = ComfyNodeError("bad graph")
    job = await create_queued(store, model_name="noobai-xl-v1.1", input_json=_input_json())
    fut = await w.enqueue(job)
    assert fut is not None
    with pytest.raises(ComfyNodeError):
        await asyncio.wait_for(fut, timeout=5.0)
    row = await get_by_id(store, job.id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_code == "comfy_error"


async def test_worker_survives_and_processes_next_job_after_failure(
    worker: tuple[QueueWorker, _FakeAdapter, _FakeS3, asyncio.Task],
    store: JobStore,
) -> None:
    """Worker doesn't crash on ComfyTimeoutError; continues to next job."""
    w, adapter, _s3, _task = worker
    adapter.wait_exc = ComfyTimeoutError("slow")
    j1 = await create_queued(store, model_name="noobai-xl-v1.1", input_json=_input_json())
    fut1 = await w.enqueue(j1)
    assert fut1 is not None
    with pytest.raises(ComfyTimeoutError):
        await asyncio.wait_for(fut1, timeout=5.0)

    # Clear the exc, enqueue a second job.
    adapter.wait_exc = None
    j2 = await create_queued(store, model_name="noobai-xl-v1.1", input_json=_input_json())
    fut2 = await w.enqueue(j2)
    assert fut2 is not None
    result = await asyncio.wait_for(fut2, timeout=5.0)
    assert isinstance(result, JobResult)


async def test_worker_handles_comfy_unreachable(
    worker: tuple[QueueWorker, _FakeAdapter, _FakeS3, asyncio.Task],
    store: JobStore,
) -> None:
    w, adapter, _s3, _task = worker
    adapter.submit_exc = ComfyUnreachableError("down")
    job = await create_queued(store, model_name="noobai-xl-v1.1", input_json=_input_json())
    fut = await w.enqueue(job)
    assert fut is not None
    with pytest.raises(ComfyUnreachableError):
        await asyncio.wait_for(fut, timeout=5.0)
    row = await get_by_id(store, job.id)
    assert row is not None
    assert row.error_code == "comfy_unreachable"
