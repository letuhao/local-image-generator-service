from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.backends.base import ModelConfig
from app.queue.jobs import create_queued, get_by_id, set_running
from app.queue.recovery import recover_jobs
from app.queue.store import JobStore
from app.queue.worker import QueueWorker
from app.registry.models import Registry


class _FakeAdapter:
    def __init__(self) -> None:
        self.client_id = "rec-client"

    async def submit(self, graph: dict) -> str:
        return "pid-rec"

    async def wait_for_completion(self, prompt_id: str, timeout_s: float) -> None:
        pass

    async def fetch_outputs(self, prompt_id: str) -> list[bytes]:
        return [b"\x89PNG\r\n\x1a\n" + b"payload"]

    async def close(self) -> None:  # pragma: no cover
        pass


class _FakeS3:
    def __init__(self) -> None:
        self.bucket = "image-gen-test"

    async def upload_png(self, job_id: str, index: int, data: bytes) -> tuple[str, str]:
        return self.bucket, f"generations/test/{job_id}/{index}.png"

    async def get_object(self, bucket: str, key: str) -> bytes:  # pragma: no cover
        raise NotImplementedError


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
            "negative_prompt": "bad",
        },
        limits={"steps_max": 60, "n_max": 4, "size_max_pixels": 1572864},
    )
    return Registry({cfg.name: cfg})


@pytest.fixture
async def worker(
    store: JobStore, registry: Registry, tmp_path: Path
) -> AsyncIterator[tuple[QueueWorker, asyncio.Task]]:
    w = QueueWorker(
        store=store,
        adapter=_FakeAdapter(),
        s3=_FakeS3(),
        registry=registry,
        public_base_url="http://testserver",
        job_timeout_s=30.0,
        max_queue=20,
        loras_root=tmp_path,
    )
    task = asyncio.create_task(w.run(), name="recovery-worker")
    try:
        yield w, task
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _body() -> str:
    return json.dumps({"model": "noobai-xl-v1.1", "prompt": "p", "size": "512x512", "steps": 1})


async def test_recovery_transitions_running_to_failed(
    store: JobStore, worker: tuple[QueueWorker, asyncio.Task]
) -> None:
    """A row with status=running on boot → failed{service_restarted}, handover=true."""
    w, _task = worker
    job = await create_queued(store, model_name="noobai-xl-v1.1", input_json=_body())
    await set_running(store, job.id, prompt_id="old-pid", client_id="old-cid")

    stats = await recover_jobs(store, w)
    assert stats["failed_restart"] == 1
    assert stats["requeued"] == 0

    row = await get_by_id(store, job.id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_code == "service_restarted"
    assert row.webhook_handover is True


async def test_recovery_requeues_queued_rows(
    store: JobStore, worker: tuple[QueueWorker, asyncio.Task]
) -> None:
    """A row with status=queued on boot → re-enqueued; worker picks it up."""
    w, _task = worker
    job = await create_queued(store, model_name="noobai-xl-v1.1", input_json=_body())
    # Row is queued — exactly the boot-time state we recover from.
    assert job.status == "queued"

    stats = await recover_jobs(store, w)
    assert stats["requeued"] == 1
    assert stats["failed_restart"] == 0

    # Wait for the worker to process.
    for _ in range(30):
        row = await get_by_id(store, job.id)
        if row is not None and row.status == "completed":
            return
        await asyncio.sleep(0.05)
    row = await get_by_id(store, job.id)
    pytest.fail(
        f"worker did not complete recovered job; final status={row.status if row else None}"
    )


async def test_recovery_leaves_completed_untouched(
    store: JobStore, worker: tuple[QueueWorker, asyncio.Task]
) -> None:
    """Rows in terminal states should not be rescanned."""
    from app.queue.jobs import set_completed

    w, _task = worker
    job = await create_queued(store, model_name="noobai-xl-v1.1", input_json=_body())
    await set_running(store, job.id, prompt_id="pid", client_id="cid")
    await set_completed(store, job.id, output_keys=["a"], result_json="{}")

    stats = await recover_jobs(store, w)
    assert stats["requeued"] == 0
    assert stats["failed_restart"] == 0
    row = await get_by_id(store, job.id)
    assert row is not None
    assert row.status == "completed"
