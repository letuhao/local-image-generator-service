from __future__ import annotations

import base64
import copy
import json
import re
import secrets
import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.auth import require_auth
from app.backends.base import (
    ComfyNodeError,
    ComfyTimeoutError,
    ComfyUnreachableError,
)
from app.queue.jobs import create_queued, set_completed, set_failed, set_running
from app.registry.workflows import find_anchor, load_workflow
from app.storage.s3 import StorageError, StorageNotFoundError
from app.validation import (
    GenerateRequest,
    ValidationFailureError,
    resolve_and_validate,
)

log = structlog.get_logger(__name__)

router = APIRouter()

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_INDEX_NAME_RE = re.compile(r"^(\d+)\.png$")


def _raise_if_not_png(data: bytes) -> None:
    if not data or not data.startswith(_PNG_MAGIC):
        raise ComfyNodeError(f"non-PNG bytes from ComfyUI (first={data[:8]!r})")


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@router.post("/v1/images/generations")
async def create_image(
    request: Request,
    kid: str = Depends(require_auth),
) -> JSONResponse:
    # 1. Parse the body — Pydantic raises ValidationError on shape/bound failures.
    try:
        raw = await request.json()
    except json.JSONDecodeError:
        return _error(400, "validation_error", "body is not valid JSON")
    try:
        body = GenerateRequest.model_validate(raw)
    except ValidationError as exc:
        return _error(400, "validation_error", exc.errors()[0]["msg"])

    # 2. Resolve against registry + enforce limits.
    registry = request.app.state.registry
    async_mode = request.app.state.async_mode_enabled
    try:
        job = resolve_and_validate(body, registry=registry, async_mode_enabled=async_mode)
    except ValidationFailureError as exc:
        status_code = 400
        return _error(status_code, exc.error_code, exc.message)

    # 3. Create persistent job row.
    store = request.app.state.store
    db_job = await create_queued(
        store, model_name=job.model.name, input_json=json.dumps(raw), mode=job.mode
    )

    # 4. Prepare workflow graph (deep copy to avoid mutating the template).
    graph_template = load_workflow(job.model.workflow_path)
    graph = copy.deepcopy(graph_template)

    # Fill in model-specific + request-specific inputs via anchors.
    pos_id = find_anchor(graph, "%POSITIVE_PROMPT%")
    neg_id = find_anchor(graph, "%NEGATIVE_PROMPT%")
    ks_id = find_anchor(graph, "%KSAMPLER%")
    graph[pos_id]["inputs"]["text"] = job.prompt
    graph[neg_id]["inputs"]["text"] = job.negative_prompt
    ks_in = graph[ks_id]["inputs"]
    # seed=-1 is the OpenAI "random" sentinel (arch §6.0). Generate a fresh seed
    # here and pin it to the actual graph so the caller can reproduce by passing
    # the same seed back. Persist both the resolved seed + input_json in result_json
    # so reproduction is possible even after the jobs row is pruned.
    actual_seed = job.seed if job.seed >= 0 else secrets.randbelow(2**53)
    ks_in["seed"] = actual_seed
    ks_in["steps"] = job.steps
    ks_in["cfg"] = job.cfg
    ks_in["sampler_name"] = job.sampler
    ks_in["scheduler"] = job.scheduler

    # Latent dims — find EmptyLatentImage by class_type (not anchor-tagged in v1).
    latent_nodes = [
        nid for nid, node in graph.items() if node.get("class_type") == "EmptyLatentImage"
    ]
    if len(latent_nodes) > 1:
        log.warning(
            "sync.multiple_latent_nodes",
            count=len(latent_nodes),
            message="only the first will receive dimension overrides",
        )
    for nid in latent_nodes[:1]:
        graph[nid]["inputs"]["width"] = job.width
        graph[nid]["inputs"]["height"] = job.height
        graph[nid]["inputs"]["batch_size"] = job.n

    # 5. Submit to ComfyUI.
    adapter = request.app.state.adapter
    started = time.perf_counter()
    try:
        prompt_id = await adapter.submit(graph)
        await set_running(
            store,
            db_job.id,
            prompt_id=prompt_id,
            client_id=getattr(adapter, "client_id", "unknown"),
        )
        await adapter.wait_for_completion(prompt_id, timeout_s=request.app.state.job_timeout_s)
        images = await adapter.fetch_outputs(prompt_id)
    except ComfyUnreachableError as exc:
        await set_failed(store, db_job.id, error_code="comfy_unreachable", error_message=str(exc))
        return _error(503, "comfy_unreachable", str(exc))
    except ComfyTimeoutError as exc:
        await set_failed(store, db_job.id, error_code="comfy_timeout", error_message=str(exc))
        return _error(504, "comfy_timeout", str(exc))
    except ComfyNodeError as exc:
        await set_failed(store, db_job.id, error_code="comfy_error", error_message=str(exc))
        return _error(500, "comfy_error", str(exc))

    # 6. Validate + upload.
    if not images:
        await set_failed(store, db_job.id, error_code="internal", error_message="empty output")
        log.error("sync.empty_output", job_id=db_job.id, prompt_id=prompt_id)
        return _error(500, "internal", "ComfyUI returned zero outputs")
    for idx, png in enumerate(images):
        try:
            _raise_if_not_png(png)
        except ComfyNodeError as exc:
            await set_failed(store, db_job.id, error_code="internal", error_message=str(exc))
            log.error("sync.non_png_bytes", job_id=db_job.id, index=idx)
            return _error(500, "internal", str(exc))

    s3 = request.app.state.s3
    output_keys: list[str] = []
    try:
        for idx, png in enumerate(images):
            bucket, key = await s3.upload_png(db_job.id, idx, png)
            output_keys.append(f"{bucket}/{key}")
    except StorageError as exc:
        await set_failed(store, db_job.id, error_code="storage_error", error_message=str(exc))
        return _error(502, "storage_error", str(exc))

    # 7. Build response.
    base_url: str = request.app.state.public_base_url
    data: list[dict[str, Any]] = []
    if job.response_format == "b64_json":
        for png in images:
            data.append({"b64_json": base64.b64encode(png).decode("ascii")})
    else:
        for idx in range(len(images)):
            data.append({"url": f"{base_url}/v1/images/{db_job.id}/{idx}.png"})

    await set_completed(
        store,
        db_job.id,
        output_keys=output_keys,
        result_json=json.dumps(
            {
                "data": data,
                "duration_ms": (time.perf_counter() - started) * 1000,
                "resolved_seed": actual_seed,  # random seed generated when caller passed -1
            }
        ),
    )

    return JSONResponse(
        status_code=200,
        content={"created": int(time.time()), "data": data},
        headers={"X-Job-Id": db_job.id},
    )


@router.api_route("/v1/images/{job_id}/{index_name}", methods=["GET", "HEAD"])
async def get_image(
    request: Request,
    job_id: str,
    index_name: str,
    kid: str = Depends(require_auth),
) -> Response:
    """Gateway: look up job, fetch bytes from S3 via our internal client, stream back."""
    m = _INDEX_NAME_RE.match(index_name)
    if not m:
        return _error(404, "not_found", f"invalid image path: {index_name}")
    index = int(m.group(1))

    store = request.app.state.store
    from app.queue.jobs import get_by_id  # local import to avoid circular

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

    return Response(content=data, media_type="image/png")
