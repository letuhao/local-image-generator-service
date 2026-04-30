from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import require_admin
from app.queue.jobs import get_by_id, list_attempts_for_job
from app.registry.models import Registry, load_registry
from app.startup.smoke_test import run_registry_smoke_tests

router = APIRouter()


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _startup_ctx(request: Request):
    ctx = getattr(request.app.state, "startup_ctx", None)
    if ctx is None:
        from app.startup.checks import build_context_from_env

        ctx = build_context_from_env()
        request.app.state.startup_ctx = ctx
    return ctx


class SetupBundleRequest(BaseModel):
    model_names: list[str] = Field(min_length=1, max_length=20)
    warmup: bool = False


@router.get("/v1/webhooks/deliveries/{job_id}")
async def get_webhook_deliveries(
    job_id: str,
    request: Request,
    kid: str = Depends(require_admin),
) -> JSONResponse:
    store = request.app.state.store
    job = await get_by_id(store, job_id)
    if job is None:
        return _error(404, "not_found", f"unknown job id {job_id!r}")
    attempts = await list_attempts_for_job(store, job_id)
    return JSONResponse(
        status_code=200,
        content={
            "job_id": job.id,
            "webhook_url": job.webhook_url,
            "webhook_delivery_status": job.webhook_delivery_status,
            "attempts": [
                {
                    "id": a.id,
                    "attempt_n": a.attempt_n,
                    "status": a.status,
                    "status_code": a.status_code,
                    "response_body_snippet": a.response_body_snippet,
                    "error": a.error,
                    "error_code": a.error_code,
                    "next_retry_at": a.next_retry_at,
                    "created_at": a.created_at,
                    "completed_at": a.completed_at,
                }
                for a in attempts
            ],
        },
    )


@router.post("/v1/admin/runtime/reload-registry")
async def reload_registry(
    request: Request,
    kid: str = Depends(require_admin),
) -> JSONResponse:
    lock: asyncio.Lock = request.app.state.runtime_reconfig_lock
    async with lock:
        ctx = _startup_ctx(request)
        registry = load_registry(
            yaml_path=ctx.models_yaml_path,
            models_root=ctx.models_root,
            workflows_root=ctx.workflows_root,
            vram_budget_gb=ctx.vram_budget_gb,
        )
        request.app.state.registry = registry
        request.app.state.worker.set_registry(registry)
        request.app.state.runtime_bundle["last_registry_reload_at"] = _now_iso()
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "models": registry.names(),
            },
        )


@router.post("/v1/admin/runtime/setup-bundle")
async def setup_bundle(
    body: SetupBundleRequest,
    request: Request,
    kid: str = Depends(require_admin),
) -> JSONResponse:
    lock: asyncio.Lock = request.app.state.runtime_reconfig_lock
    async with lock:
        registry = request.app.state.registry
        unknown = [name for name in body.model_names if name not in set(registry.names())]
        if unknown:
            return _error(404, "unknown_model", f"unknown model(s): {unknown}")

        if body.warmup:
            subset = Registry({name: registry.get(name) for name in body.model_names})
            await run_registry_smoke_tests(
                adapter=request.app.state.adapter,
                registry=subset,
                loras_root=request.app.state.loras_root,
                timeout_s=60.0,
            )

        request.app.state.runtime_bundle = {
            "active_models": body.model_names,
            "warmup": body.warmup,
            "updated_at": _now_iso(),
        }
        return JSONResponse(status_code=200, content={"ok": True, "runtime_bundle": request.app.state.runtime_bundle})


@router.post("/v1/admin/runtime/close-bundle")
async def close_bundle(
    request: Request,
    kid: str = Depends(require_admin),
) -> JSONResponse:
    lock: asyncio.Lock = request.app.state.runtime_reconfig_lock
    async with lock:
        unloaded = await request.app.state.adapter.unload_models(verify_timeout_s=30.0)
        request.app.state.runtime_bundle = {
            "active_models": [],
            "warmup": False,
            "updated_at": _now_iso(),
            "closed": True,
        }
        return JSONResponse(
            status_code=200,
            content={"ok": True, "unloaded": unloaded, "runtime_bundle": request.app.state.runtime_bundle},
        )

