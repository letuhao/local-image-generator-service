from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Literal

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.api.init_image_multipart import merge_init_image_upload, parse_payload_object
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
    VideoGenerateRequest,
    ValidationFailureError,
    resolve_and_validate_video,
)

log = structlog.get_logger(__name__)

router = APIRouter()

_MEDIA_INDEX_RE = re.compile(r"^(\d+)\.(mp4|webm|gif|png)$")
_DISCONNECT_POLL_S = 0.5

_GATEWAY_MEDIA_TYPES = {
    "mp4": "video/mp4",
    "webm": "video/webm",
    "gif": "image/gif",
    "png": "image/png",
}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


async def _watch_disconnect(
    request: Request, store, job_id: str, interval: float = _DISCONNECT_POLL_S
) -> None:
    try:
        while True:
            if await request.is_disconnected():
                await mark_async_with_handover(store, job_id)
                log.info("sync.video_client_disconnected", job_id=job_id)
                return
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise


async def _run_video_job(
    request: Request,
    *,
    raw: dict,
    body: VideoGenerateRequest,
    video_task: Literal["t2v", "i2v"],
):
    registry = request.app.state.registry
    async_mode = request.app.state.async_mode_enabled
    validated = resolve_and_validate_video(
        body,
        registry=registry,
        async_mode_enabled=async_mode,
        expected_task=video_task,
        loras_root=request.app.state.loras_root,
    )

    store = request.app.state.store
    max_queue = request.app.state.max_queue
    active = await count_active(store)
    if active >= max_queue:
        log.info("sync.video_queue_full", active=active, max_queue=max_queue)
        return None, None, _error(
            429,
            "queue_full",
            f"queue depth {active} >= MAX_QUEUE {max_queue}",
        )

    envelope = {
        "artifact_kind": "video",
        "video_task": video_task,
        "payload": raw,
    }
    db_job = await create_queued(
        store,
        model_name=body.model,
        input_json=json.dumps(envelope),
        mode=body.mode,
        webhook_url=validated.webhook_url,
        webhook_headers=validated.webhook_headers,
    )
    metrics = getattr(request.app.state, "metrics", None)
    if metrics is not None:
        metrics.record_job_event(status="queued")

    worker = request.app.state.worker
    if body.mode == "async":
        await worker.enqueue_detached(db_job)
        return db_job, None, None

    fut = await worker.enqueue(db_job)

    watcher = asyncio.create_task(
        _watch_disconnect(request, store, db_job.id),
        name=f"disconnect-video-watcher-{db_job.id}",
    )

    try:
        result = await asyncio.shield(fut)
    except ComfyUnreachableError as exc:
        watcher.cancel()
        return None, None, _error(503, "comfy_unreachable", str(exc))
    except ComfyTimeoutError as exc:
        watcher.cancel()
        return None, None, _error(504, "comfy_timeout", str(exc))
    except ComfyNodeError as exc:
        watcher.cancel()
        return None, None, _error(500, "comfy_error", str(exc))
    except StorageError as exc:
        watcher.cancel()
        return None, None, _error(502, "storage_error", str(exc))
    finally:
        if not watcher.done():
            watcher.cancel()

    return db_job, result, None


def _poll_video_body(job: Job) -> dict:
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


@router.post("/v1/videos/generations/text-to-video")
async def create_text_to_video(
    request: Request,
    background_tasks: BackgroundTasks,
    kid: str = Depends(require_auth),
) -> JSONResponse:
    runtime_lock = getattr(request.app.state, "runtime_reconfig_lock", None)
    if runtime_lock is not None and runtime_lock.locked():
        return _error(503, "runtime_reconfiguring", "runtime bundle is being reconfigured")

    try:
        raw = await request.json()
    except json.JSONDecodeError:
        return _error(400, "validation_error", "body is not valid JSON")
    try:
        body = VideoGenerateRequest.model_validate(raw)
    except ValidationError as exc:
        return _error(400, "validation_error", exc.errors()[0]["msg"])

    try:
        db_job, result, early_error = await _run_video_job(
            request, raw=raw, body=body, video_task="t2v"
        )
    except ValidationFailureError as exc:
        return _error(400, exc.error_code, exc.message)
    if early_error is not None:
        return early_error
    assert db_job is not None

    if body.mode == "async":
        store = request.app.state.store
        background_tasks.add_task(mark_initial_response_delivered, store, db_job.id)
        return JSONResponse(
            status_code=202,
            content={"id": db_job.id, "status": "processing"},
            headers={"X-Job-Id": db_job.id},
            background=background_tasks,
        )

    assert result is not None

    store = request.app.state.store
    background_tasks.add_task(mark_response_delivered, store, db_job.id)

    return JSONResponse(
        status_code=200,
        content={"created": int(time.time()), "data": result.data},
        headers={"X-Job-Id": db_job.id},
        background=background_tasks,
    )


@router.post("/v1/videos/generations/image-to-video")
async def create_image_to_video(
    request: Request,
    background_tasks: BackgroundTasks,
    kid: str = Depends(require_auth),
) -> JSONResponse:
    runtime_lock = getattr(request.app.state, "runtime_reconfig_lock", None)
    if runtime_lock is not None and runtime_lock.locked():
        return _error(503, "runtime_reconfiguring", "runtime bundle is being reconfigured")

    try:
        raw = await request.json()
    except json.JSONDecodeError:
        return _error(400, "validation_error", "body is not valid JSON")
    try:
        body = VideoGenerateRequest.model_validate(raw)
    except ValidationError as exc:
        return _error(400, "validation_error", exc.errors()[0]["msg"])

    try:
        db_job, result, early_error = await _run_video_job(
            request, raw=raw, body=body, video_task="i2v"
        )
    except ValidationFailureError as exc:
        return _error(400, exc.error_code, exc.message)
    if early_error is not None:
        return early_error
    assert db_job is not None

    if body.mode == "async":
        store = request.app.state.store
        background_tasks.add_task(mark_initial_response_delivered, store, db_job.id)
        return JSONResponse(
            status_code=202,
            content={"id": db_job.id, "status": "processing"},
            headers={"X-Job-Id": db_job.id},
            background=background_tasks,
        )

    assert result is not None

    store = request.app.state.store
    background_tasks.add_task(mark_response_delivered, store, db_job.id)

    return JSONResponse(
        status_code=200,
        content={"created": int(time.time()), "data": result.data},
        headers={"X-Job-Id": db_job.id},
        background=background_tasks,
    )


@router.post("/v1/videos/generations/image-to-video/multipart")
async def create_image_to_video_multipart(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: str = Form(
        ...,
        description=(
            "JSON object matching VideoGenerateRequest without init_image "
            "(supplied by the image file)"
        ),
    ),
    image: UploadFile = File(..., description="First-frame / reference image"),
    kid: str = Depends(require_auth),
) -> JSONResponse:
    """Same as POST .../image-to-video but init_image is uploaded as multipart binary."""
    runtime_lock = getattr(request.app.state, "runtime_reconfig_lock", None)
    if runtime_lock is not None and runtime_lock.locked():
        return _error(503, "runtime_reconfiguring", "runtime bundle is being reconfigured")

    try:
        raw_obj = parse_payload_object(payload)
        image_bytes = await image.read()
        raw = merge_init_image_upload(raw_obj, image_bytes)
    except ValueError as exc:
        return _error(400, "validation_error", str(exc))

    try:
        body = VideoGenerateRequest.model_validate(raw)
    except ValidationError as exc:
        return _error(400, "validation_error", exc.errors()[0]["msg"])

    try:
        db_job, result, early_error = await _run_video_job(
            request, raw=raw, body=body, video_task="i2v"
        )
    except ValidationFailureError as exc:
        return _error(400, exc.error_code, exc.message)
    if early_error is not None:
        return early_error
    assert db_job is not None

    if body.mode == "async":
        store = request.app.state.store
        background_tasks.add_task(mark_initial_response_delivered, store, db_job.id)
        return JSONResponse(
            status_code=202,
            content={"id": db_job.id, "status": "processing"},
            headers={"X-Job-Id": db_job.id},
            background=background_tasks,
        )

    assert result is not None

    store = request.app.state.store
    background_tasks.add_task(mark_response_delivered, store, db_job.id)

    return JSONResponse(
        status_code=200,
        content={"created": int(time.time()), "data": result.data},
        headers={"X-Job-Id": db_job.id},
        background=background_tasks,
    )


@router.get("/v1/videos/generations/{job_id}")
async def get_video_generation_status(
    job_id: str,
    request: Request,
    kid: str = Depends(require_auth),
) -> JSONResponse:
    store = request.app.state.store
    job = await get_by_id(store, job_id)
    if job is None:
        return _error(404, "not_found", "unknown job id")
    return JSONResponse(status_code=200, content=_poll_video_body(job))


@router.api_route("/v1/videos/{job_id}/{index_name}", methods=["GET", "HEAD"])
async def get_video_asset(
    request: Request,
    job_id: str,
    index_name: str,
    kid: str = Depends(require_auth),
) -> Response:
    m = _MEDIA_INDEX_RE.match(index_name)
    if not m:
        return _error(404, "not_found", f"invalid video path: {index_name}")
    index = int(m.group(1))
    ext = m.group(2)

    store = request.app.state.store
    job = await get_by_id(store, job_id)
    if job is None or job.status != "completed" or not job.output_keys:
        return _error(404, "not_found", f"no media for {job_id}/{index}")
    if index < 0 or index >= len(job.output_keys):
        return _error(404, "not_found", f"index {index} out of range")

    bucket, _, key = job.output_keys[index].partition("/")
    if not key.lower().endswith(f".{ext}"):
        return _error(404, "not_found", "asset extension mismatch")

    s3 = request.app.state.s3
    try:
        data = await s3.get_object(bucket, key)
    except StorageNotFoundError:
        return _error(404, "not_found", "media bytes no longer available")
    except StorageError as exc:
        return _error(502, "storage_error", str(exc))

    await set_fetched(store, job_id)

    media_type = _GATEWAY_MEDIA_TYPES.get(ext, "application/octet-stream")
    return Response(content=data, media_type=media_type)
