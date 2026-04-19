from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger(__name__)

# X-Request-Id: conservative — alphanumerics + hyphen, 1..128 chars. Matches what
# common gateways (cloudfront, cloudflare) emit. Anything else → fall back to uuid4.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,128}$")


def _pick_request_id(inbound: str | None) -> str:
    if inbound and _REQUEST_ID_RE.match(inbound):
        return inbound
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Per-request: bind request_id contextvar, emit an access line, echo header.

    Implemented as a pure ASGI middleware (not BaseHTTPMiddleware) because the
    latter wraps requests in anyio task groups, which re-raises exceptions as
    ExceptionGroup and breaks FastAPI's exception-handler chain for unhandled
    errors. Pure ASGI lets Starlette's ExceptionMiddleware see raw exceptions.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound: str | None = None
        for name, value in scope.get("headers", ()):
            if name == b"x-request-id":
                inbound = value.decode("latin-1")
                break
        request_id = _pick_request_id(inbound)

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                # Strip any existing X-Request-Id a route may have set, then append ours.
                headers = [(k, v) for k, v in headers if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # Float ms with 3-decimal (microsecond) resolution. Sub-ms requests
            # show as e.g. 0.317 instead of being truncated to 0.
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            log.info(
                "request.served",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
