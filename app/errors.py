from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger(__name__)


def _code_for_status(status_code: int) -> str:
    if status_code in (401, 403):
        return "auth_error"
    if status_code == 404:
        return "not_found"
    return "internal"


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Unwrap HTTPException.detail if it's already in {"error": {...}} shape.

    Keeps spec §12.8 envelope: `{"error": {"code": ..., "message": ...}}`.
    Catches both FastAPI's HTTPException (subclass) and Starlette's route-level 404s.
    """
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        body = detail
    else:
        body = {"error": {"code": _code_for_status(exc.status_code), "message": str(detail)}}
    # Preserve headers (e.g. WWW-Authenticate on 401 — see app.auth.AuthError).
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler for any exception not caught by a more specific handler.

    Arch §13 requires error_code on every 4xx/5xx response. Without this,
    unhandled exceptions return plain text and break LoreWeave's error switch.
    The full traceback is logged via structlog (redacted by redact_sensitive);
    the response body carries only the opaque `internal` code.
    """
    log.exception(
        "request.unhandled_exception",
        exc_type=type(exc).__name__,
        path=request.url.path,
        method=request.method,
    )
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal", "message": "Internal Server Error"}},
    )


def install_error_envelope(app: FastAPI) -> None:
    """Register the unwrapping HTTPException handler + the generic fallback.

    Registering against Starlette's HTTPException (parent of FastAPI's HTTPException)
    catches route-level 404s + 405s alongside our own AuthError / etc.
    The generic Exception handler guarantees every 5xx carries the envelope.
    """
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
