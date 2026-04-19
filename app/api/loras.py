from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from app.auth import require_auth
from app.loras.scanner import scan_loras

router = APIRouter()


@router.get("/v1/loras")
async def list_loras(
    request: Request,
    kid: str = Depends(require_auth),
) -> dict:
    """List LoRAs under `LORAS_ROOT`. Arch §6.5. Any auth scope.

    Entries marked `addressable=false` cannot be referenced in POST /v1/images/generations
    until the on-disk filename is renamed to match the request-name regex.

    Scanner does synchronous disk I/O — `stat()` per file + optional JSON read.
    We hand it off to a worker thread so concurrent requests on the event loop
    don't serialize behind each other's scans.
    """
    root = request.app.state.loras_root
    metas = await asyncio.to_thread(scan_loras, root)
    return {
        "object": "list",
        "data": [
            {
                "name": m.name,
                "filename": m.filename,
                "sha256": m.sha256,
                "source": m.source,
                "civitai_model_id": m.civitai_model_id,
                "civitai_version_id": m.civitai_version_id,
                "base_model_hint": m.base_model_hint,
                "trigger_words": list(m.trigger_words),
                "fetched_at": m.fetched_at,
                "size_bytes": m.size_bytes,
                "addressable": m.addressable,
                "reason": m.reason,
                "sidecar_status": m.sidecar_status,
            }
            for m in metas
        ],
    }
