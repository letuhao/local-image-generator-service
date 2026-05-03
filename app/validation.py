from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel, ConfigDict, Field

from app.backends.base import ModelConfig
from app.registry.models import Registry
from app.registry.workflows import ResolvedLoraRef

log = structlog.get_logger(__name__)

# Debounce last_used sidecar writes to avoid write amplification: 20 loras ×
# 10 req/s = 200 writes/sec otherwise. A 5-min window is fine-grained enough
# for eviction decisions (we only care about day-scale freshness) and
# eliminates ~99% of the writes on a hot path.
LORA_LAST_USED_DEBOUNCE_S_ENV = "LORA_LAST_USED_DEBOUNCE_S"
_DEFAULT_DEBOUNCE_S = 300
VRAM_BUDGET_GB_ENV = "VRAM_BUDGET_GB"
_DEFAULT_VRAM_BUDGET_GB = 12.0
_LORA_VRAM_OVERHEAD_GB = 0.064

# Allowed ComfyUI samplers + schedulers per arch §6.0. Restrict to the well-supported set;
# additions land as registry changes with corresponding workflow updates.
ALLOWED_SAMPLERS: frozenset[str] = frozenset(
    {
        "euler",
        "euler_ancestral",
        "heun",
        "dpm_2",
        "dpm_2_ancestral",
        "lms",
        "dpmpp_2s_ancestral",
        "dpmpp_sde",
        "dpmpp_2m",
        "dpmpp_2m_sde",
        "dpmpp_3m_sde",
        "ddim",
        "uni_pc",
    }
)

ALLOWED_SCHEDULERS: frozenset[str] = frozenset(
    {
        "normal",
        "karras",
        "exponential",
        "sgm_uniform",
        "simple",
        "ddim_uniform",
    }
)

ALLOWED_SAMPLERS_BY_FAMILY: dict[str, frozenset[str]] = {
    "sdxl": ALLOWED_SAMPLERS,
    "flux": frozenset({"euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde"}),
    # Qwen-Anime / Qwen Image Edit (Beta3-AIO): Euler-focused; same tight set as Flux.
    "qwen": frozenset({"euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde"}),
}

ALLOWED_SCHEDULERS_BY_FAMILY: dict[str, frozenset[str]] = {
    "sdxl": ALLOWED_SCHEDULERS,
    "flux": frozenset({"simple", "karras", "normal"}),
    "qwen": frozenset({"simple", "karras", "normal"}),
}


class ValidationFailureError(Exception):
    """Raised by resolve_and_validate when registry-dependent checks fail.

    The API layer catches this and emits a 400 with the carried error_code.
    """

    def __init__(self, *, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class LoraSpec(BaseModel):
    """Per-LoRA request entry. Name is a POSIX-style path relative to `LORAS_ROOT`
    (subdirs allowed, no `.safetensors` suffix). Weight covers both
    `strength_model` and `strength_clip` per arch §9."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_/\-.]*$",
        max_length=256,
    )
    weight: float = Field(ge=-2.0, le=2.0)


_RESERVED_WEBHOOK_HEADERS = {
    "host",
    "authorization",
    "content-type",
    "user-agent",
}
_WEBHOOK_HEADER_KEY_RE = re.compile(r"^[A-Za-z0-9\-_]{1,64}$")


class WebhookSpec(BaseModel):
    """Optional caller-supplied webhook destination + passthrough headers."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, str] | None = Field(default=None, max_length=10)


class GenerateRequest(BaseModel):
    """Pydantic validation of the POST /v1/images/generations body per arch §6.0."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model: str
    prompt: str = Field(min_length=1, max_length=8000)
    negative_prompt: str | None = Field(default=None, max_length=2000)
    size: str = Field(pattern=r"^\d{3,4}x\d{3,4}$", default="1024x1024")
    n: int = Field(ge=1, default=1)
    steps: int | None = Field(ge=1, default=None)
    cfg: float = Field(ge=0, le=30, default=5.0)
    seed: int = Field(ge=-1, le=(2**53), default=-1)
    # When present, must be non-empty; empty string is a client bug, not a signal to
    # use the default. Matches `prompt`'s min_length=1 posture.
    sampler: str | None = Field(default=None, min_length=1)
    scheduler: str | None = Field(default=None, min_length=1)
    response_format: Literal["url", "b64_json"] = "url"
    transparent_background: bool = True
    mode: Literal["sync", "async"] = "sync"
    timeout_s: float | None = Field(default=None, ge=1.0, le=3600.0)
    loras: list[LoraSpec] | None = Field(default=None, max_length=20)
    init_image: str | None = Field(default=None, description="Base64 encoded image or data URI")
    webhook: WebhookSpec | None = None


@dataclass(frozen=True, slots=True)
class ValidatedJob:
    """Fully-resolved request: Pydantic-valid + registry-looked-up + limits-enforced."""

    model: ModelConfig
    prompt: str
    negative_prompt: str
    size: str
    width: int
    height: int
    n: int
    steps: int
    cfg: float
    seed: int
    sampler: str
    scheduler: str
    response_format: Literal["url", "b64_json"]
    transparent_background: bool
    mode: Literal["sync", "async"]
    timeout_s: float | None
    webhook_url: str | None = None
    webhook_headers: dict[str, str] | None = None
    loras: tuple[ResolvedLoraRef, ...] = field(default=())
    init_image_bytes: bytes | None = field(default=None, repr=False)


def _parse_size(size: str) -> tuple[int, int]:
    w_str, h_str = size.lower().split("x", 1)
    return int(w_str), int(h_str)


def _vram_budget_gb() -> float:
    raw = os.environ.get(VRAM_BUDGET_GB_ENV)
    try:
        return float(raw) if raw is not None else _DEFAULT_VRAM_BUDGET_GB
    except ValueError:
        return _DEFAULT_VRAM_BUDGET_GB


def resolve_and_validate(
    req: GenerateRequest,
    *,
    registry: Registry,
    async_mode_enabled: bool,
    loras_root: Path,
) -> ValidatedJob:
    """Merge model defaults + enforce model-scoped limits.

    Raises ValidationFailureError with an arch §13 error_code on any violation.
    Pydantic-level failures (shape, regex, range) raise earlier at model_validate.

    `loras_root` must be the already-resolved (absolute) LoRA root. The caller
    (app.main lifespan → app.state.loras_root) owns resolution so validator,
    worker recovery, and GET /v1/loras all share one source of truth.
    """
    # 1. Model must exist in registry.
    try:
        model = registry.get(req.model)
    except KeyError:
        raise ValidationFailureError(
            error_code="validation_error",
            message=f"unknown model: {req.model!r}",
        ) from None

    defaults = model.defaults or {}
    limits = model.limits or {}

    # 2. Merge defaults for fields the caller omitted.
    size = req.size or defaults.get("size") or "1024x1024"
    n = req.n
    steps = req.steps if req.steps is not None else defaults.get("steps", 28)
    cfg = req.cfg
    sampler = req.sampler or defaults.get("sampler", "euler_ancestral")
    scheduler = req.scheduler or defaults.get("scheduler", "karras")
    negative_prompt = (
        req.negative_prompt
        if req.negative_prompt is not None
        else defaults.get("negative_prompt", "")
    )

    # 3. Enum checks on sampler/scheduler — family-aware while keeping a
    # universal payload shape for clients.
    allowed_samplers = ALLOWED_SAMPLERS_BY_FAMILY.get(model.family, ALLOWED_SAMPLERS)
    allowed_schedulers = ALLOWED_SCHEDULERS_BY_FAMILY.get(model.family, ALLOWED_SCHEDULERS)
    if sampler not in allowed_samplers:
        raise ValidationFailureError(
            error_code="validation_error",
            message=(
                f"sampler {sampler!r} not in allowed set for family={model.family!r}: "
                f"{sorted(allowed_samplers)}"
            ),
        )
    if scheduler not in allowed_schedulers:
        raise ValidationFailureError(
            error_code="validation_error",
            message=(
                f"scheduler {scheduler!r} not in allowed set for family={model.family!r}: "
                f"{sorted(allowed_schedulers)}"
            ),
        )

    # 4. Size bounds against model.limits.size_max_pixels.
    try:
        width, height = _parse_size(size)
    except ValueError as exc:
        raise ValidationFailureError(
            error_code="validation_error",
            message=f"size {size!r} malformed",
        ) from exc
    size_max_pixels = int(limits.get("size_max_pixels", 1048576))
    if width * height > size_max_pixels:
        raise ValidationFailureError(
            error_code="validation_error",
            message=(
                f"size {width}x{height}={width * height}px exceeds "
                f"model.limits.size_max_pixels={size_max_pixels}"
            ),
        )

    # 5. n / steps against model.limits.
    n_max = int(limits.get("n_max", 1))
    if n > n_max:
        raise ValidationFailureError(
            error_code="validation_error",
            message=f"n={n} exceeds model.limits.n_max={n_max}",
        )
    steps_max = int(limits.get("steps_max", 50))
    if steps > steps_max:
        raise ValidationFailureError(
            error_code="validation_error",
            message=f"steps={steps} exceeds model.limits.steps_max={steps_max}",
        )

    # 6. Async mode gate.
    if req.mode == "async" and not async_mode_enabled:
        raise ValidationFailureError(
            error_code="async_not_enabled",
            message="mode=async requires ASYNC_MODE_ENABLED=true",
        )

    webhook_url: str | None = None
    webhook_headers: dict[str, str] | None = None
    if req.webhook is not None:
        parsed = urlparse(req.webhook.url)
        if parsed.scheme not in {"http", "https"}:
            raise ValidationFailureError(
                error_code="validation_error",
                message="webhook.url must use http or https",
            )
        if not parsed.hostname:
            raise ValidationFailureError(
                error_code="validation_error",
                message="webhook.url must include a host",
            )
        webhook_url = req.webhook.url
        if req.webhook.headers:
            normalized: dict[str, str] = {}
            for k, v in req.webhook.headers.items():
                lk = k.lower()
                if lk in _RESERVED_WEBHOOK_HEADERS or lk.startswith("x-imagegen-"):
                    raise ValidationFailureError(
                        error_code="validation_error",
                        message=f"webhook.headers key {k!r} is reserved",
                    )
                if not _WEBHOOK_HEADER_KEY_RE.match(k):
                    raise ValidationFailureError(
                        error_code="validation_error",
                        message=f"webhook.headers key {k!r} has invalid characters",
                    )
                if len(v) > 256:
                    raise ValidationFailureError(
                        error_code="validation_error",
                        message=f"webhook.headers key/value length invalid for {k!r}",
                    )
                normalized[k] = v
            webhook_headers = normalized

    # 7. LoRA resolution: realpath-contain each reference under LORAS_ROOT, then
    #    confirm the .safetensors exists on disk. Realpath resolution catches
    #    symlink-escape attempts (e.g. a dev drops a symlink under LORAS_ROOT that
    #    points at /etc/passwd) — name-regex alone wouldn't catch that.
    resolved_loras: tuple[ResolvedLoraRef, ...] = ()
    if req.loras:
        resolved_list: list[ResolvedLoraRef] = []
        for spec in req.loras:
            target = (loras_root / f"{spec.name}.safetensors").resolve()
            try:
                target.relative_to(loras_root)
            except ValueError:
                raise ValidationFailureError(
                    error_code="validation_error",
                    message=f"lora name {spec.name!r} escapes loras root",
                ) from None
            if not target.is_file():
                raise ValidationFailureError(
                    error_code="lora_missing",
                    message=f"lora file not found: {spec.name}",
                )
            resolved_list.append(ResolvedLoraRef(name=spec.name, weight=spec.weight))
        resolved_loras = tuple(resolved_list)

    # 8. Request-time VRAM guard (arch §11): model estimate + coarse LoRA overhead.
    vram_needed = float(model.vram_estimate_gb) + (_LORA_VRAM_OVERHEAD_GB * len(resolved_loras))
    budget = _vram_budget_gb()
    if vram_needed > budget:
        details = (
            f"model={model.vram_estimate_gb:.3f} + "
            f"loras={len(resolved_loras)}*{_LORA_VRAM_OVERHEAD_GB:.3f}"
        )
        raise ValidationFailureError(
            error_code="vram_budget_exceeded",
            message=(
                f"estimated vram {vram_needed:.3f} GB exceeds budget {budget:.3f} GB ({details})"
            ),
        )

    init_image_bytes: bytes | None = None
    if req.init_image:
        # Strip data URI prefix if present: data:image/png;base64,...
        b64_str = req.init_image
        if b64_str.startswith("data:image"):
            try:
                b64_str = b64_str.split(",", 1)[1]
            except IndexError:
                pass
        try:
            init_image_bytes = base64.b64decode(b64_str, validate=True)
        except ValueError as exc:
            raise ValidationFailureError(
                error_code="validation_error",
                message="init_image must be a valid base64 string",
            ) from exc

    return ValidatedJob(
        model=model,
        prompt=req.prompt,
        negative_prompt=negative_prompt,
        size=size,
        width=width,
        height=height,
        n=n,
        steps=steps,
        cfg=cfg,
        seed=req.seed,
        sampler=sampler,
        scheduler=scheduler,
        response_format=req.response_format,
        transparent_background=req.transparent_background,
        mode=req.mode,
        timeout_s=req.timeout_s,
        webhook_url=webhook_url,
        webhook_headers=webhook_headers,
        loras=resolved_loras,
        init_image_bytes=init_image_bytes,
    )


# ── Sidecar last_used tracking (Cycle 6) ─────────────────────────────────────


def _debounce_seconds() -> int:
    """Re-read env each call so tests can override via monkeypatch.setenv."""
    raw = os.environ.get(LORA_LAST_USED_DEBOUNCE_S_ENV)
    try:
        return int(raw) if raw is not None else _DEFAULT_DEBOUNCE_S
    except ValueError:
        return _DEFAULT_DEBOUNCE_S


def _touch_last_used_sync(sidecar_path: Path) -> None:
    """Best-effort: update sidecar.last_used if stale beyond debounce window.

    - Missing sidecar (user hand-drop): skip silently (γ-protection).
    - Existing last_used within debounce window: skip (write amplification guard).
    - Read/parse/write failure: log WARN and proceed — never block generation on
      an observability update.
    """
    if not sidecar_path.is_file():
        return
    try:
        text = sidecar_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "lora.last_used.read_failed",
            sidecar=str(sidecar_path),
            error=str(exc),
        )
        return
    if not isinstance(data, dict):
        return

    now = datetime.now(UTC)
    prev = data.get("last_used")
    if isinstance(prev, str):
        try:
            prev_dt = datetime.fromisoformat(prev)
            if prev_dt.tzinfo is None:
                prev_dt = prev_dt.replace(tzinfo=UTC)
            if (now - prev_dt).total_seconds() < _debounce_seconds():
                return  # still fresh; debounce skip
        except ValueError:
            # Malformed ISO-8601 — fall through and overwrite with a good value.
            pass

    data["last_used"] = now.isoformat()
    tmp = sidecar_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, sidecar_path)
    except OSError as exc:
        log.warning(
            "lora.last_used.write_failed",
            sidecar=str(sidecar_path),
            error=str(exc),
        )
        # Best-effort cleanup of partial tmp.
        try:
            tmp.unlink()
        except OSError:
            pass


async def touch_last_used_async(loras_root: Path, resolved: tuple[ResolvedLoraRef, ...]) -> None:
    """Handler-side helper: fire sidecar touches for each resolved LoRA in a
    thread pool so they don't block the event loop. Called AFTER
    resolve_and_validate succeeds."""
    if not resolved:
        return
    for lora in resolved:
        sidecar = (loras_root / f"{lora.name}.safetensors").with_suffix(".json")
        await asyncio.to_thread(_touch_last_used_sync, sidecar)
