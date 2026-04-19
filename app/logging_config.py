from __future__ import annotations

import logging
import re
from typing import Any

import structlog

# Keys whose values are always dropped from log records, at any nesting depth.
_DROP_KEYS: frozenset[str] = frozenset(
    {
        "Authorization",
        "authorization",
        "presigned_url",
        "presigned_urls",
        "X-Amz-Signature",
        "x-amz-signature",
        "webhook_signing_secret",
        "api_key",
        "api_keys",
    }
)
# Keys whose values are redacted (replaced with "<redacted>") at any nesting depth,
# unless BOTH LOG_PROMPTS=true AND the log call is at DEBUG.
_REDACTABLE_KEYS: frozenset[str] = frozenset({"prompt", "negative_prompt"})

# String-level patterns: scan values of "event" and "exception" for secrets that
# leaked via f-string formatting or traceback frame locals. The three common
# shapes: `Bearer <token>`, `X-Amz-Signature=<sig>`, `Authorization: Bearer <token>`.
# Pattern boundaries chosen to be conservative — we'd rather over-redact than leak.
_STRING_SCRUB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/=]+"), "Bearer <redacted>"),
    (re.compile(r"(?i)X-Amz-Signature=[A-Za-z0-9%]+"), "X-Amz-Signature=<redacted>"),
    (re.compile(r"(?i)Authorization:\s*[^\r\n]+"), "Authorization: <redacted>"),
)

# Module-level posture set by configure_logging; read by redact_sensitive.
_log_prompts: bool = False
_effective_level: int = logging.INFO


def configure_logging(level: str = "INFO", log_prompts: bool = False) -> None:
    """Idempotent. Configure structlog + the stdlib bridge.

    Must be called before any log is emitted. Safe to call again to flip level
    or the LOG_PROMPTS flag.
    """
    global _log_prompts, _effective_level
    _log_prompts = bool(log_prompts)
    numeric = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    _effective_level = numeric

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_sensitive,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    root = logging.getLogger()
    root.handlers = []
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(numeric)

    # uvicorn.error / uvicorn propagate to our JSON formatter.
    # uvicorn.access is silenced entirely — RequestContextMiddleware emits the canonical
    # access line with request_id, status, duration_ms. Two access lines per request
    # (one from uvicorn, one from us) would double-count in log analytics.
    for name in ("uvicorn.error", "uvicorn"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(numeric)
    access_lg = logging.getLogger("uvicorn.access")
    access_lg.handlers = []
    access_lg.propagate = False
    access_lg.disabled = True


def _scrub_string(value: str) -> str:
    for pattern, replacement in _STRING_SCRUB_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _walk_and_redact(obj: Any, reveal_prompts: bool) -> Any:
    """Recursively apply redaction rules to dicts and lists.

    - Drops _DROP_KEYS at any depth.
    - Replaces _REDACTABLE_KEYS values with "<redacted>" unless reveal_prompts.
    - Leaves other values untouched. String scrubbing is applied separately to
      the top-level `event` and `exception` fields only (see redact_sensitive).
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _DROP_KEYS:
                continue
            if k in _REDACTABLE_KEYS and not reveal_prompts:
                out[k] = "<redacted>"
            else:
                out[k] = _walk_and_redact(v, reveal_prompts)
        return out
    if isinstance(obj, list):
        return [_walk_and_redact(item, reveal_prompts) for item in obj]
    return obj


def redact_sensitive(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Structlog processor. Scrubs secrets at all depths.

    Rules:
      - Drops keys listed in _DROP_KEYS (Authorization, presigned_url, ...) at any depth.
      - Replaces prompt/negative_prompt with "<redacted>" at any depth, unless BOTH
        LOG_PROMPTS=true AND the log call is at DEBUG level.
      - Applies regex-based scrubbing to the top-level `event` and `exception` fields,
        catching `Bearer <tok>`, `X-Amz-Signature=...`, and `Authorization: ...` leaks
        that slipped through via f-string formatting or frame-local exception rendering.

    Known limitations:
      - Secrets inside unknown string fields (other than event/exception) are NOT
        regex-scanned — add fields to _STRING_SCRUB_PATTERNS or keep them out of logs.
      - Custom log-level names (e.g. `log.log(25, ...)`) won't match the DEBUG gate;
        prompts will still redact, which is the safe default.
    """
    reveal_prompts = _log_prompts and method_name.lower() == "debug"
    redacted = _walk_and_redact(event_dict, reveal_prompts)
    # String-level scrub on the two fields most likely to carry leaked secrets.
    for field in ("event", "exception"):
        val = redacted.get(field)
        if isinstance(val, str):
            redacted[field] = _scrub_string(val)
    return redacted
