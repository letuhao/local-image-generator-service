from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app import __version__
from app.api.health import router as health_router
from app.auth import load_keyset_from_env
from app.errors import install_error_envelope
from app.logging_config import configure_logging
from app.middleware.logging import RequestContextMiddleware
from app.queue.store import JobStore

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
    app.state.keyset = load_keyset_from_env()

    log.info(
        "service.started",
        version=__version__,
        imagegen_env=os.environ.get("IMAGEGEN_ENV", "dev"),
        generation_keys=len(app.state.keyset.generation),
        admin_keys=len(app.state.keyset.admin),
    )

    try:
        yield
    finally:
        log.info("service.stopping")
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

# RequestContextMiddleware added LAST so it wraps everything (outermost layer).
app.add_middleware(RequestContextMiddleware)
