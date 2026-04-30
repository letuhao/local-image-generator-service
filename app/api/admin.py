from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth import require_admin
from app.queue.jobs import get_by_id, list_attempts_for_job

router = APIRouter()


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


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

