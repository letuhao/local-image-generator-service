from __future__ import annotations

import asyncio
import json
import re
import time

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.auth import require_auth
from app.backends.base import (
    ComfyNodeError,
    ComfyTimeoutError,
    ComfyUnreachableError,
)
from app.queue.jobs import (
    Job,
    count_active,
    create_queued,
    get_by_id,
    mark_async_with_handover,
    mark_initial_response_delivered,
    mark_response_delivered,
    set_fetched,
)
from app.storage.s3 import StorageError, StorageNotFoundError
from app.validation import (
    GenerateRequest,
    ValidationFailureError,
    resolve_and_validate,
    touch_last_used_async,
)

log = structlog.get_logger(__name__)

router = APIRouter()

_INDEX_NAME_RE = re.compile(r"^(\d+)\.png$")
_DISCONNECT_POLL_S = 0.5


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


async def _watch_disconnect(
    request: Request, store, job_id: str, interval: float = _DISCONNECT_POLL_S
) -> None:
    """Side-task: poll `is_disconnected` and flip the row on first detected drop."""
    try:
        while True:
            if await request.is_disconnected():
                await mark_async_with_handover(store, job_id)
                log.info("sync.client_disconnected", job_id=job_id)
                return
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        # Normal path: handler finished before disconnect; nothing to do.
        raise


@router.post("/v1/images/generations")
async def create_image(
    request: Request,
    background_tasks: BackgroundTasks,
    kid: str = Depends(require_auth),
) -> JSONResponse:
    runtime_lock = getattr(request.app.state, "runtime_reconfig_lock", None)
    if runtime_lock is not None and runtime_lock.locked():
        return _error(503, "runtime_reconfiguring", "runtime bundle is being reconfigured")

    # 1. Parse + Pydantic validate.
    try:
        raw = await request.json()
    except json.JSONDecodeError:
        return _error(400, "validation_error", "body is not valid JSON")
    try:
        body = GenerateRequest.model_validate(raw)
    except ValidationError as exc:
        return _error(400, "validation_error", exc.errors()[0]["msg"])

    # 2. Resolve registry + enforce limits.
    registry = request.app.state.registry
    async_mode = request.app.state.async_mode_enabled
    loras_root = request.app.state.loras_root
    try:
        validated = resolve_and_validate(
            body,
            registry=registry,
            async_mode_enabled=async_mode,
            loras_root=loras_root,
        )
    except ValidationFailureError as exc:
        return _error(400, exc.error_code, exc.message)

    # Update LoRA sidecar last_used timestamps (debounced, best-effort, thread-
    # pooled). Fires only on successful validation so we don't touch sidecars
    # for rejected requests.
    await touch_last_used_async(loras_root, validated.loras)

    # 3. MAX_QUEUE gate — SQLite ground truth.
    store = request.app.state.store
    max_queue = request.app.state.max_queue
    active = await count_active(store)
    if active >= max_queue:
        log.info("sync.queue_full", active=active, max_queue=max_queue)
        return _error(
            429,
            "queue_full",
            f"queue depth {active} >= MAX_QUEUE {max_queue}",
        )

    # 4. Persist job.
    db_job = await create_queued(
        store,
        model_name=body.model,
        input_json=json.dumps(raw),
        mode=body.mode,
        webhook_url=validated.webhook_url,
        webhook_headers=validated.webhook_headers,
    )

    # 5. Pure async — return 202 immediately; worker runs without a waiter Future.
    worker = request.app.state.worker
    if body.mode == "async":
        await worker.enqueue_detached(db_job)
        background_tasks.add_task(mark_initial_response_delivered, store, db_job.id)
        return JSONResponse(
            status_code=202,
            content={"id": db_job.id, "status": "processing"},
            headers={"X-Job-Id": db_job.id},
            background=background_tasks,
        )

    fut = await worker.enqueue(db_job)

    # 6. Disconnect watcher side-task.
    watcher = asyncio.create_task(
        _watch_disconnect(request, store, db_job.id),
        name=f"disconnect-watcher-{db_job.id}",
    )

    # 7. Await completion under shield so client disconnect cancelling the handler
    #    doesn't also cancel the worker's future resolution.
    try:
        result = await asyncio.shield(fut)
    except ComfyUnreachableError as exc:
        watcher.cancel()
        return _error(503, "comfy_unreachable", str(exc))
    except ComfyTimeoutError as exc:
        watcher.cancel()
        return _error(504, "comfy_timeout", str(exc))
    except ComfyNodeError as exc:
        watcher.cancel()
        return _error(500, "comfy_error", str(exc))
    except StorageError as exc:
        watcher.cancel()
        return _error(502, "storage_error", str(exc))
    finally:
        if not watcher.done():
            watcher.cancel()

    # 8. Schedule response_delivered flush; runs after response bytes emit.
    background_tasks.add_task(mark_response_delivered, store, db_job.id)

    return JSONResponse(
        status_code=200,
        content={"created": int(time.time()), "data": result.data},
        headers={"X-Job-Id": db_job.id},
        background=background_tasks,
    )


def _poll_body(job: Job) -> dict:
    """Shape for `GET /v1/images/generations/{id}` per arch §6.3."""
    wh = job.webhook_delivery_status
    base: dict = {"id": job.id, "status": job.status, "webhook_delivery_status": wh}
    if job.status == "completed":
        parsed = json.loads(job.result_json) if job.result_json else {}
        base["data"] = parsed.get("data", [])
        return base
    if job.status in ("failed", "abandoned"):
        base["error"] = {
            "code": job.error_code or job.status,
            "message": job.error_message or "",
        }
        return base
    return base


@router.get("/v1/images/generations/{job_id}")
async def get_generation_status(
    job_id: str,
    request: Request,
    kid: str = Depends(require_auth),
) -> JSONResponse:
    """Poll async (or inspect sync) jobs — arch §6.3."""
    store = request.app.state.store
    job = await get_by_id(store, job_id)
    if job is None:
        return _error(404, "not_found", "unknown job id")
    return JSONResponse(status_code=200, content=_poll_body(job))


@router.api_route("/v1/images/{job_id}/{index_name}", methods=["GET", "HEAD"])
async def get_image(
    request: Request,
    job_id: str,
    index_name: str,
    kid: str = Depends(require_auth),
) -> Response:
    """Gateway: look up job, fetch bytes from S3 via internal client, stream back."""
    m = _INDEX_NAME_RE.match(index_name)
    if not m:
        return _error(404, "not_found", f"invalid image path: {index_name}")
    index = int(m.group(1))

    store = request.app.state.store
    job = await get_by_id(store, job_id)
    if job is None or job.status != "completed" or not job.output_keys:
        return _error(404, "not_found", f"no image for {job_id}/{index}")
    if index < 0 or index >= len(job.output_keys):
        return _error(404, "not_found", f"index {index} out of range")

    bucket, _, key = job.output_keys[index].partition("/")
    s3 = request.app.state.s3
    try:
        data = await s3.get_object(bucket, key)
    except StorageNotFoundError:
        return _error(404, "not_found", "image bytes no longer available")
    except StorageError as exc:
        return _error(502, "storage_error", str(exc))

    # Cycle 4: mark first-fetch timestamp for the orphan reaper.
    await set_fetched(store, job_id)

    return Response(content=data, media_type="image/png")
