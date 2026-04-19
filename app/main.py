from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app import __version__
from app.api.health import router as health_router
from app.api.images import router as images_router
from app.api.models import router as models_router
from app.auth import load_keyset_from_env
from app.backends.comfyui import ComfyUIAdapter
from app.errors import install_error_envelope
from app.logging_config import configure_logging
from app.middleware.logging import RequestContextMiddleware
from app.queue.store import JobStore
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

    log.info(
        "service.started",
        version=__version__,
        imagegen_env=os.environ.get("IMAGEGEN_ENV", "dev"),
        generation_keys=len(app.state.keyset.generation),
        admin_keys=len(app.state.keyset.admin),
        models=registry.names(),
        public_base_url=app.state.public_base_url,
    )

    try:
        yield
    finally:
        log.info("service.stopping")
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

# RequestContextMiddleware added LAST so it wraps everything (outermost layer).
app.add_middleware(RequestContextMiddleware)
