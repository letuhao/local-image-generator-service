from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.backends.base import ModelConfig
from app.registry.models import Registry
from app.registry.workflows import ResolvedLoraRef

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


class GenerateRequest(BaseModel):
    """Pydantic validation of the POST /v1/images/generations body per arch §6.0.

    `extra="forbid"` → unknown fields (webhook — Cycle 9) are rejected now so
    callers can't silently lose data once that lands.
    """

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
    mode: Literal["sync", "async"] = "sync"
    loras: list[LoraSpec] | None = Field(default=None, max_length=20)


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
    mode: Literal["sync", "async"]
    loras: tuple[ResolvedLoraRef, ...] = field(default=())


def _parse_size(size: str) -> tuple[int, int]:
    w_str, h_str = size.lower().split("x", 1)
    return int(w_str), int(h_str)


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

    # 3. Enum checks on sampler/scheduler — arch §6.0.
    if sampler not in ALLOWED_SAMPLERS:
        raise ValidationFailureError(
            error_code="validation_error",
            message=f"sampler {sampler!r} not in allowed set",
        )
    if scheduler not in ALLOWED_SCHEDULERS:
        raise ValidationFailureError(
            error_code="validation_error",
            message=f"scheduler {scheduler!r} not in allowed set",
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

    # 7. LoRA resolution: realpath-contain each reference under LORAS_ROOT, then
    #    confirm the .safetensors exists on disk. Realpath resolution catches
    #    symlink-escape attempts (e.g. a dev drops a symlink under ./loras/ that
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
        mode=req.mode,
        loras=resolved_loras,
    )
