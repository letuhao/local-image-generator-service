from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from app.auth import require_auth

router = APIRouter()


@router.get("/v1/models")
async def list_models(
    request: Request,
    kid: str = Depends(require_auth),
) -> dict:
    """OpenAI-compatible model list, plus capabilities + backend per arch §6.4."""
    registry = request.app.state.registry
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": cfg.name,
                "object": "model",
                "created": now,
                "owned_by": "local",
                "capabilities": cfg.capabilities,
                "backend": cfg.backend,
            }
            for cfg in registry.all()
        ],
    }
