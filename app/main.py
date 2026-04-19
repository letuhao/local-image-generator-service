from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI

from app import __version__
from app.api.health import router as health_router
from app.api.images import router as images_router
from app.api.loras import router as loras_router
from app.api.models import router as models_router
from app.auth import load_keyset_from_env
from app.backends.comfyui import ComfyUIAdapter
from app.errors import install_error_envelope
from app.logging_config import configure_logging
from app.middleware.logging import RequestContextMiddleware
from app.queue.reaper import OrphanReaper
from app.queue.recovery import recover_jobs
from app.queue.store import JobStore
from app.queue.worker import QueueWorker
from app.registry.models import load_registry
from app.storage.s3 import S3Config, S3Storage

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        log_prompts=os.environ.get("LOG_PROMPTS", "false").lower() == "true",
    )

    store = JobStore(os.environ.get("DATABASE_PATH", "/app/data/jobs.db"))
    await store.connect()
    app.state.store = store

    # Registry — fail-fast validation at startup.
    registry = load_registry(
        yaml_path=os.environ.get("MODELS_YAML_PATH", "config/models.yaml"),
        models_root=os.environ.get("MODELS_ROOT", "./models"),
        workflows_root=os.environ.get("WORKFLOWS_ROOT", "."),
        vram_budget_gb=float(os.environ.get("VRAM_BUDGET_GB", "12")),
    )
    app.state.registry = registry

    # S3 storage — bucket ensured at boot (idempotent).
    s3 = S3Storage(S3Config.from_env())
    await s3.ensure_bucket()
    app.state.s3 = s3

    # ComfyUI adapter — lazy (no network until first request).
    adapter = ComfyUIAdapter(
        http_url=os.environ.get("COMFYUI_URL", "http://comfyui:8188"),
        ws_url=os.environ.get("COMFYUI_WS_URL", "ws://comfyui:8188/ws"),
        http_timeout_s=float(os.environ.get("COMFY_HTTP_TIMEOUT_S", "30")),
        poll_interval_ms=int(os.environ.get("COMFY_POLL_INTERVAL_MS", "1000")),
    )
    app.state.adapter = adapter

    app.state.keyset = load_keyset_from_env()
    app.state.async_mode_enabled = os.environ.get("ASYNC_MODE_ENABLED", "false").lower() == "true"
    public_base_url = os.environ.get("IMAGE_GEN_PUBLIC_BASE_URL", "http://127.0.0.1:8700").rstrip(
        "/"
    )
    if not public_base_url.startswith(("http://", "https://")):
        raise RuntimeError(
            "IMAGE_GEN_PUBLIC_BASE_URL must start with http:// or https://, "
            f"got {public_base_url!r}"
        )
    app.state.public_base_url = public_base_url
    app.state.job_timeout_s = float(os.environ.get("JOB_TIMEOUT_S", "300"))
    max_queue = int(os.environ.get("MAX_QUEUE", "20"))
    app.state.max_queue = max_queue

    # LoRA root — served by GET /v1/loras and consulted by validation for
    # realpath containment. Resolved once at boot so the validator does not
    # re-resolve from CWD per-request (CWD can drift in tests). Startup is
    # allowed to block on disk; lifespan wraps everything.
    app.state.loras_root = Path(  # noqa: ASYNC240
        os.environ.get("LORAS_ROOT", "./loras")
    ).resolve()

    # Cycle 4: queue worker + orphan reaper + restart recovery.
    # IMPORTANT ordering: worker must be running BEFORE recover_jobs calls
    # worker.enqueue_recovery (blocking put), otherwise recovery deadlocks on
    # a full queue waiting for a consumer.
    worker = QueueWorker(
        store=store,
        adapter=adapter,
        s3=s3,
        registry=registry,
        public_base_url=public_base_url,
        job_timeout_s=app.state.job_timeout_s,
        max_queue=max_queue,
        loras_root=app.state.loras_root,
        async_mode_enabled=app.state.async_mode_enabled,
    )
    app.state.worker = worker
    app.state.worker_task = asyncio.create_task(worker.run(), name="queue-worker")

    reaper = OrphanReaper(
        store=store,
        s3=s3,
        ttl_seconds=int(os.environ.get("ORPHAN_REAPER_TTL", "86400")),
        scan_interval_seconds=int(os.environ.get("ORPHAN_REAPER_SCAN_INTERVAL_S", "600")),
    )
    app.state.reaper = reaper
    app.state.reaper_task = asyncio.create_task(reaper.run(), name="orphan-reaper")

    # Recovery scan. Worker + reaper are already spawned above.
    recovery_stats = await recover_jobs(store, worker)

    log.info(
        "service.started",
        version=__version__,
        imagegen_env=os.environ.get("IMAGEGEN_ENV", "dev"),
        generation_keys=len(app.state.keyset.generation),
        admin_keys=len(app.state.keyset.admin),
        models=registry.names(),
        public_base_url=app.state.public_base_url,
        recovery=recovery_stats,
    )

    try:
        yield
    finally:
        log.info("service.stopping")
        # Hard-cancel only. Arch §12 specifies SHUTDOWN_GRACE_S=90 for a drain
        # period that waits for the active GPU job; that's Cycle 10's work.
        # Cycle 4 clients in flight at shutdown receive 500s (handler cancelled).
        for task_attr in ("reaper_task", "worker_task"):
            task = getattr(app.state, task_attr, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await adapter.close()
        await store.close()


app = FastAPI(
    title="image-gen-service",
    description="Local OpenAI-compatible image generation microservice for LoreWeave",
    version=__version__,
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

install_error_envelope(app)
app.include_router(health_router)
app.include_router(images_router)
app.include_router(models_router)
app.include_router(loras_router)

# RequestContextMiddleware added LAST so it wraps everything (outermost layer).
app.add_middleware(RequestContextMiddleware)
