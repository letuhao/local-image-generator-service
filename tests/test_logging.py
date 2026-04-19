from __future__ import annotations

import io
import json
import logging
from collections.abc import AsyncIterator, Iterator

import pytest
import structlog
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.logging_config import configure_logging, redact_sensitive
from app.middleware.logging import RequestContextMiddleware

# ───────────────────────── processor-level redaction ─────────────────────────


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def test_redact_sensitive_drops_authorization_and_presigned_url() -> None:
    configure_logging(level="INFO", log_prompts=False)
    event = {
        "event": "x",
        "Authorization": "Bearer abc",
        "authorization": "Bearer abc",
        "presigned_url": "https://s3/...?X-Amz-Signature=...",
        "safe": "keep",
    }
    out = redact_sensitive(None, "info", event)
    assert "Authorization" not in out
    assert "authorization" not in out
    assert "presigned_url" not in out
    assert out["safe"] == "keep"


def test_redact_sensitive_redacts_prompt_when_log_prompts_false() -> None:
    configure_logging(level="DEBUG", log_prompts=False)
    event = {"event": "x", "prompt": "some nsfw content", "negative_prompt": "bad"}
    out = redact_sensitive(None, "debug", event)
    assert out["prompt"] == "<redacted>"
    assert out["negative_prompt"] == "<redacted>"


def test_redact_sensitive_redacts_prompt_at_info_even_with_log_prompts_true() -> None:
    configure_logging(level="INFO", log_prompts=True)
    event = {"event": "x", "prompt": "some prompt"}
    out = redact_sensitive(None, "info", event)
    assert out["prompt"] == "<redacted>"


def test_redact_sensitive_renders_prompt_when_debug_and_log_prompts_true() -> None:
    configure_logging(level="DEBUG", log_prompts=True)
    event = {"event": "x", "prompt": "renderable"}
    out = redact_sensitive(None, "debug", event)
    assert out["prompt"] == "renderable"


def test_redact_sensitive_drops_sensitive_keys_in_nested_dict() -> None:
    """Nested dicts must have _DROP_KEYS stripped at any depth."""
    configure_logging(level="INFO", log_prompts=False)
    event = {
        "event": "x",
        "ctx": {"Authorization": "Bearer leaked", "safe": "keep"},
        "deep": {"inner": {"presigned_url": "https://s3/..."}},
    }
    out = redact_sensitive(None, "info", event)
    assert "Authorization" not in out["ctx"]
    assert out["ctx"]["safe"] == "keep"
    assert "presigned_url" not in out["deep"]["inner"]


def test_redact_sensitive_redacts_prompt_in_nested_dict() -> None:
    configure_logging(level="INFO", log_prompts=False)
    event = {"event": "x", "req": {"prompt": "nested"}}
    out = redact_sensitive(None, "info", event)
    assert out["req"]["prompt"] == "<redacted>"


def test_redact_sensitive_scrubs_bearer_in_event_string() -> None:
    """A f-string-formatted leak into `event` gets regex-scrubbed."""
    configure_logging(level="INFO", log_prompts=False)
    event = {"event": "got Bearer sk-abc123 from client"}
    out = redact_sensitive(None, "info", event)
    assert "sk-abc123" not in out["event"]
    assert "Bearer <redacted>" in out["event"]


def test_redact_sensitive_scrubs_bearer_in_exception_string() -> None:
    """Tracebacks from format_exc_info get the same scrub."""
    configure_logging(level="INFO", log_prompts=False)
    event = {
        "event": "oops",
        "exception": 'Traceback (most recent call last):\n  token="Bearer eyJhbGciOi"',
    }
    out = redact_sensitive(None, "info", event)
    assert "eyJhbGciOi" not in out["exception"]
    assert "Bearer <redacted>" in out["exception"]


def test_redact_sensitive_scrubs_amz_signature() -> None:
    configure_logging(level="INFO", log_prompts=False)
    event = {"event": "signed url: https://s3/x?X-Amz-Signature=abcdef1234"}
    out = redact_sensitive(None, "info", event)
    assert "abcdef1234" not in out["event"]
    assert "X-Amz-Signature=<redacted>" in out["event"]


# ───────────────────────── JSON shape via captured stdlib stream ─────────────


@pytest.fixture
def json_stream() -> Iterator[io.StringIO]:
    """Swap stdlib root handler stream to capture JSON lines."""
    configure_logging(level="INFO", log_prompts=False)
    buf = io.StringIO()
    root = logging.getLogger()
    original = list(root.handlers)
    handler = logging.StreamHandler(buf)
    handler.setFormatter(original[0].formatter if original else logging.Formatter())
    root.handlers = [handler]
    try:
        yield buf
    finally:
        root.handlers = original


def test_log_line_is_json_with_required_fields(json_stream: io.StringIO) -> None:
    log = structlog.get_logger("test")
    log.info("unit.event", extra_field="value")
    line = json_stream.getvalue().strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["event"] == "unit.event"
    assert parsed["level"] == "info"
    assert "timestamp" in parsed
    assert parsed["extra_field"] == "value"


def test_contextvars_bound_appear_in_log(json_stream: io.StringIO) -> None:
    structlog.contextvars.bind_contextvars(request_id="req-123", key_id="abcd1234")
    structlog.get_logger("test").info("bound.event")
    line = json_stream.getvalue().strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["request_id"] == "req-123"
    assert parsed["key_id"] == "abcd1234"


# ───────────────────────── middleware integration ─────────────────────────


def _build_app_with_middleware() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/echo")
    async def echo() -> dict:
        ctx = structlog.contextvars.get_contextvars()
        return {"request_id": ctx.get("request_id")}

    return app


@pytest.fixture
async def middleware_client() -> AsyncIterator[AsyncClient]:
    configure_logging(level="INFO", log_prompts=False)
    app = _build_app_with_middleware()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def test_middleware_generates_request_id_when_absent(middleware_client: AsyncClient) -> None:
    resp = await middleware_client.get("/echo")
    assert resp.status_code == 200
    request_id = resp.json()["request_id"]
    assert request_id
    assert len(request_id) <= 128
    assert resp.headers["x-request-id"] == request_id


async def test_middleware_respects_inbound_x_request_id(middleware_client: AsyncClient) -> None:
    resp = await middleware_client.get("/echo", headers={"X-Request-Id": "client-abc-123"})
    assert resp.status_code == 200
    assert resp.json()["request_id"] == "client-abc-123"
    assert resp.headers["x-request-id"] == "client-abc-123"


async def test_middleware_rejects_malformed_x_request_id(middleware_client: AsyncClient) -> None:
    """Header values with illegal chars or over length → middleware falls back to a fresh id."""
    bad_header = "a" * 200  # too long
    resp = await middleware_client.get("/echo", headers={"X-Request-Id": bad_header})
    assert resp.status_code == 200
    assert resp.json()["request_id"] != bad_header
