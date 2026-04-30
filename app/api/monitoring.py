from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.auth import require_admin
from app.queue.jobs import count_active, count_by_status

router = APIRouter()


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/metrics")
async def metrics_prometheus(request: Request) -> PlainTextResponse:
    metrics = getattr(request.app.state, "metrics", None)
    if metrics is None:
        return PlainTextResponse("monitoring not configured\n", status_code=503)
    active_jobs = await count_active(request.app.state.store)
    content = metrics.render_prometheus(
        queue_depth=request.app.state.worker.queue_depth(),
        worker_concurrency=request.app.state.worker_concurrency,
        active_jobs=active_jobs,
    )
    return PlainTextResponse(content=content, media_type="text/plain; version=0.0.4")


@router.get("/v1/admin/monitoring/status")
async def monitoring_status(request: Request, kid: str = Depends(require_admin)) -> JSONResponse:
    metrics = getattr(request.app.state, "metrics", None)
    if metrics is None:
        return _error(503, "monitoring_unavailable", "monitoring metrics collector is not configured")
    adapter_health = {"status": "unknown", "vram_free_gb": None}
    health_fn = getattr(request.app.state.adapter, "health", None)
    if callable(health_fn):
        try:
            adapter_health = await health_fn()
        except Exception:
            adapter_health = {"status": "error", "vram_free_gb": None}
    status = str(adapter_health.get("status", "unknown"))
    vram_raw = adapter_health.get("vram_free_gb")
    try:
        vram_free_gb = float(vram_raw) if vram_raw is not None else None
    except (TypeError, ValueError):
        vram_free_gb = None
    metrics.update_comfy_health(status=status, vram_free_gb=vram_free_gb)
    snapshot = metrics.admin_snapshot()
    return JSONResponse(
        status_code=200,
        content={
            "at": _now_iso(),
            "runtime_bundle": request.app.state.runtime_bundle,
            "worker_last_model_name": request.app.state.worker.last_model_name(),
            "worker_concurrency": request.app.state.worker_concurrency,
            "runtime_reconfiguring": request.app.state.runtime_reconfig_lock.locked(),
            "queue_depth": request.app.state.worker.queue_depth(),
            "active_jobs": await count_active(request.app.state.store),
            "monitoring": snapshot,
        },
    )


@router.get("/v1/admin/monitoring/queue")
async def monitoring_queue(request: Request, kid: str = Depends(require_admin)) -> JSONResponse:
    status_counts = await count_by_status(request.app.state.store)
    return JSONResponse(
        status_code=200,
        content={
            "queue_depth": request.app.state.worker.queue_depth(),
            "active_jobs": await count_active(request.app.state.store),
            "max_queue": request.app.state.max_queue,
            "worker_concurrency": request.app.state.worker_concurrency,
            "job_status_counts": status_counts,
        },
    )
