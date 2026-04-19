from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.auth import verify_key

router = APIRouter()


@router.api_route("/health", methods=["GET", "HEAD"])
async def get_health(
    request: Request, authorization: str | None = Header(default=None)
) -> JSONResponse:
    store = request.app.state.store
    keyset = request.app.state.keyset
    db_ok = await store.healthcheck()
    verbose = verify_key(authorization, keyset)

    if db_ok:
        body = {"status": "ok", "db": "ok"} if verbose else {"status": "ok"}
        return JSONResponse(status_code=200, content=body)

    body = {"status": "degraded", "db": "unreachable"} if verbose else {"status": "degraded"}
    return JSONResponse(status_code=503, content=body)
