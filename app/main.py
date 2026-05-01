from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import structlog
from fastapi import FastAPI

from app import __version__
from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.api.images import router as images_router
from app.api.loras import router as loras_router
from app.api.models import router as models_router
from app.api.monitoring import router as monitoring_router
from app.auth import load_keyset_from_env
from app.backends.comfyui import ComfyUIAdapter
from app.errors import install_error_envelope
from app.logging_config import configure_logging
from app.loras.civitai import CivitaiFetcher
from app.middleware.logging import RequestContextMiddleware
from app.monitoring.metrics import MonitoringMetrics
from app.queue.fetches_recovery import recover_fetches
from app.queue.reaper import OrphanReaper
from app.queue.recovery import recover_jobs
from app.queue.store import JobStore
from app.queue.worker import QueueWorker
from app.registry.presets import PresetRegistryValidationError, load_preset_registry
from app.startup.checks import StartupCheckError, build_context_from_env, run_startup_checks
from app.startup.smoke_test import StartupSmokeError, run_registry_smoke_tests
from app.storage.s3 import S3Config, S3Storage
from app.webhooks.dispatcher import WebhookDispatcher

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        log_prompts=os.environ.get("LOG_PROMPTS", "false").lower() == "true",
    )

    startup_ctx = build_context_from_env()
    app.state.startup_ctx = startup_ctx
    app.state.runtime_reconfig_lock = asyncio.Lock()
    app.state.runtime_bundle = {"active_models": [], "warmup": False, "updated_at": None}
    app.state.metrics = MonitoringMetrics()
    try:
        store = JobStore(os.environ.get("DATABASE_PATH", "/app/data/jobs.db"))
        await store.connect()
        app.state.store = store

        # Registry and startup posture checks.
        registry = run_startup_checks(startup_ctx)
        app.state.registry = registry
        presets_yaml_path = Path(os.environ.get("PRESETS_YAML_PATH", "config/presets/catalog.yaml"))
        app.state.preset_registry = load_preset_registry(presets_yaml_path, models=registry)

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
        app.state.async_mode_enabled = (
            os.environ.get("ASYNC_MODE_ENABLED", "false").lower() == "true"
        )
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
        worker_concurrency = int(os.environ.get("WORKER_CONCURRENCY", "1"))
        if worker_concurrency < 1:
            raise RuntimeError(
                f"WORKER_CONCURRENCY must be >= 1, got {worker_concurrency}"
            )
        app.state.max_queue = max_queue
        app.state.worker_concurrency = worker_concurrency

    # LoRA root — served by GET /v1/loras and consulted by validation for
    # realpath containment. Resolved once at boot so the validator does not
    # re-resolve from CWD per-request (CWD can drift in tests). Startup is
    # allowed to block on disk; lifespan wraps everything.
        app.state.loras_root = Path(  # noqa: ASYNC240
            os.environ.get("LORAS_ROOT", "./models/loras")
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
            metrics=app.state.metrics,
        )
        app.state.worker = worker
        app.state.worker_tasks = [
            asyncio.create_task(worker.run(), name=f"queue-worker-{idx + 1}")
            for idx in range(worker_concurrency)
        ]

        reaper = OrphanReaper(
            store=store,
            s3=s3,
            ttl_seconds=int(os.environ.get("ORPHAN_REAPER_TTL", "86400")),
            scan_interval_seconds=int(os.environ.get("ORPHAN_REAPER_SCAN_INTERVAL_S", "600")),
        )
        app.state.reaper = reaper
        app.state.reaper_task = asyncio.create_task(reaper.run(), name="orphan-reaper")

        webhook_http = httpx.AsyncClient()
        webhook_dispatcher = WebhookDispatcher(store=store, http_client=webhook_http)
        app.state.webhook_http = webhook_http
        app.state.webhook_dispatcher = webhook_dispatcher
        app.state.webhook_dispatcher_task = asyncio.create_task(
            webhook_dispatcher.run(),
            name="webhook-dispatcher",
        )

        startup_smoke_enabled = os.environ.get("STARTUP_SMOKE_ENABLED", "true").lower() == "true"
        if startup_smoke_enabled:
            # Cycle 10 startup smoke: run one tiny generation per registered model.
            await run_registry_smoke_tests(
                adapter=adapter,
                registry=registry,
                loras_root=app.state.loras_root,
                timeout_s=float(os.environ.get("STARTUP_SMOKE_TIMEOUT_S", "120")),
            )
        else:
            log.info("startup_smoke.skipped", reason="STARTUP_SMOKE_ENABLED=false")

        # Recovery scan. Worker + reaper are already spawned above.
        recovery_stats = await recover_jobs(store, worker)

        # Cycle 6: CivitaiFetcher + lora_fetches recovery. Install BEFORE recovery
        # scans the table so a handover-flip row is consistent with the fetcher's
        # in-flight set (always empty at boot).
        fetcher_http = httpx.AsyncClient()
        fetcher = CivitaiFetcher(
            store=store,
            loras_root=app.state.loras_root,
            api_token=os.environ.get("CIVITAI_API_TOKEN") or None,
            http_client=fetcher_http,
            dir_max_bytes=int(float(os.environ.get("LORA_DIR_MAX_SIZE_GB", "60")) * (1024**3)),
            file_max_bytes=int(os.environ.get("LORA_MAX_SIZE_BYTES", "2147483648")),
            recent_use_days=int(os.environ.get("LORA_RECENT_USE_DAYS", "7")),
            max_concurrent=int(os.environ.get("LORA_MAX_CONCURRENT_FETCHES", "1")),
            metadata_timeout_s=float(os.environ.get("LORA_FETCH_METADATA_TIMEOUT_S", "30")),
            download_overall_timeout_s=float(
                os.environ.get("LORA_FETCH_DOWNLOAD_OVERALL_TIMEOUT_S", "1800")
            ),
            chunk_read_timeout_s=float(os.environ.get("LORA_FETCH_CHUNK_READ_TIMEOUT_S", "30")),
        )
        app.state.fetcher = fetcher
        app.state.fetcher_http = fetcher_http
        fetch_recovery_stats = await recover_fetches(store, app.state.loras_root)

        log.info(
            "service.started",
            version=__version__,
            imagegen_env=os.environ.get("IMAGEGEN_ENV", "dev"),
            generation_keys=len(app.state.keyset.generation),
            admin_keys=len(app.state.keyset.admin),
            models=registry.names(),
            public_base_url=app.state.public_base_url,
            recovery=recovery_stats,
            fetch_recovery=fetch_recovery_stats,
        )
    except (StartupCheckError, StartupSmokeError, PresetRegistryValidationError) as exc:
        log.error("startup_failed", stage=getattr(exc, "stage", "smoke_test"), reason=str(exc))
        worker_tasks = getattr(app.state, "worker_tasks", None)
        if worker_tasks:
            for task in worker_tasks:
                if task is not None and not task.done():
                    task.cancel()
        for task_attr in ("reaper_task", "webhook_dispatcher_task"):
            task = getattr(app.state, task_attr, None)
            if task is not None and not task.done():
                task.cancel()
        fetcher = getattr(app.state, "fetcher", None)
        if fetcher is not None:
            await fetcher.close()
        fetcher_http = getattr(app.state, "fetcher_http", None)
        if fetcher_http is not None:
            await fetcher_http.aclose()
        webhook_http = getattr(app.state, "webhook_http", None)
        if webhook_http is not None:
            await webhook_http.aclose()
        adapter = getattr(app.state, "adapter", None)
        if adapter is not None:
            await adapter.close()
        store = getattr(app.state, "store", None)
        if store is not None:
            await store.close()
        raise RuntimeError(f"startup_failed: {exc}") from exc

    try:
        yield
    finally:
        log.info("service.stopping")
        # Hard-cancel only. Arch §12 specifies SHUTDOWN_GRACE_S=90 for a drain
        # period that waits for the active GPU job; that's Cycle 10's work.
        # Cycle 4 clients in flight at shutdown receive 500s (handler cancelled).
        # Cycle 6: cancel fetcher first so its per-version locks release before
        # store.close() rips the DB connection.
        await fetcher.close()
        worker_tasks = getattr(app.state, "worker_tasks", [])
        for task in worker_tasks:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for task_attr in ("reaper_task", "webhook_dispatcher_task"):
            task = getattr(app.state, task_attr, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        try:
            await fetcher_http.aclose()
        except Exception as exc:
            log.warning("fetcher_http.close_failed", error=str(exc))
        try:
            await webhook_http.aclose()
        except Exception as exc:
            log.warning("webhook_http.close_failed", error=str(exc))
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
app.include_router(admin_router)
app.include_router(monitoring_router)

# RequestContextMiddleware added LAST so it wraps everything (outermost layer).
app.add_middleware(RequestContextMiddleware)
