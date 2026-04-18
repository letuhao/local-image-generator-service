from __future__ import annotations

from fastapi import FastAPI

from app import __version__

app = FastAPI(
    title="image-gen-service",
    description="Local OpenAI-compatible image generation microservice for LoreWeave",
    version=__version__,
    docs_url="/docs",
    redoc_url=None,
)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict[str, str]:
    """Liveness probe. HEAD is supported for load balancers that probe with it."""
    return {"status": "ok"}
