from __future__ import annotations

import asyncio
import json

import aiosqlite
import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.auth import require_admin, require_auth
from app.loras.civitai_url import parse_civitai_url
from app.loras.scanner import scan_loras
from app.queue.fetches import (
    create_pending,
    find_active_by_version,
)
from app.queue.fetches import (
    get_by_id as get_fetch_by_id,
)

log = structlog.get_logger(__name__)

router = APIRouter()


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


class CivitaiFetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=512)


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
                "last_used": m.last_used,
                "size_bytes": m.size_bytes,
                "addressable": m.addressable,
                "reason": m.reason,
                "sidecar_status": m.sidecar_status,
            }
            for m in metas
        ],
    }


# ─── Civitai fetch — Cycle 6 ─────────────────────────────────────────


@router.post("/v1/loras/fetch", status_code=202)
async def post_fetch(
    request: Request,
    kid: str = Depends(require_admin),
) -> JSONResponse:
    """Schedule a Civitai fetch. Admin scope. Idempotent on concurrent requests
    for the same (model_id, version_id)."""
    try:
        raw = await request.json()
    except json.JSONDecodeError:
        return _error(400, "validation_error", "body is not valid JSON")
    try:
        body = CivitaiFetchRequest.model_validate(raw)
    except ValidationError as exc:
        return _error(400, "validation_error", exc.errors()[0]["msg"])

    try:
        parsed = parse_civitai_url(body.url)
    except ValueError as exc:
        return _error(400, "validation_error", str(exc))

    store = request.app.state.store
    fetcher = request.app.state.fetcher

    # Dedupe by active version_id. First check without insert to return the
    # existing request_id cleanly; then attempt INSERT, catching IntegrityError
    # in case a concurrent sibling beat us to the partial-index.
    existing = await find_active_by_version(store, parsed.version_id)
    if existing is not None:
        return JSONResponse(
            status_code=202,
            content={
                "request_id": existing.id,
                "poll_url": f"/v1/loras/fetch/{existing.id}",
                "deduped": True,
            },
        )
    try:
        row = await create_pending(
            store,
            url=body.url,
            civitai_model_id=parsed.model_id,
            civitai_version_id=parsed.version_id,
        )
    except aiosqlite.IntegrityError:
        # A concurrent sibling won the race; return its request_id.
        existing = await find_active_by_version(store, parsed.version_id)
        if existing is None:
            # Extreme corner: sibling transitioned to terminal already. Very
            # unlikely given the partial index scope. Surface as unavailable.
            return _error(
                503,
                "internal",
                "fetch could not be scheduled; retry",
            )
        return JSONResponse(
            status_code=202,
            content={
                "request_id": existing.id,
                "poll_url": f"/v1/loras/fetch/{existing.id}",
                "deduped": True,
            },
        )

    fetcher.enqueue(row.id)
    return JSONResponse(
        status_code=202,
        content={
            "request_id": row.id,
            "poll_url": f"/v1/loras/fetch/{row.id}",
            "deduped": False,
        },
    )


@router.get("/v1/loras/fetch/{request_id}")
async def get_fetch_status(
    request: Request,
    request_id: str,
    kid: str = Depends(require_admin),
) -> JSONResponse:
    """Poll a fetch's state. Admin scope."""
    store = request.app.state.store
    row = await get_fetch_by_id(store, request_id)
    if row is None:
        return _error(404, "not_found", f"no fetch with id {request_id!r}")
    return JSONResponse(
        status_code=200,
        content={
            "id": row.id,
            "url": row.url,
            "civitai_model_id": row.civitai_model_id,
            "civitai_version_id": row.civitai_version_id,
            "status": row.status,
            "progress_bytes": row.progress_bytes,
            "total_bytes": row.total_bytes,
            "dest_name": row.dest_name,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "handover": row.handover,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        },
    )
