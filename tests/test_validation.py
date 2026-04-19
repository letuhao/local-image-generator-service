from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.backends.base import ModelConfig
from app.registry.models import Registry
from app.validation import (
    ALLOWED_SAMPLERS,
    ALLOWED_SCHEDULERS,
    GenerateRequest,
    ValidationFailureError,
    resolve_and_validate,
)


@pytest.fixture
def registry() -> Registry:
    cfg = ModelConfig(
        name="noobai-xl-v1.1",
        backend="comfyui",
        workflow_path="workflows/sdxl_eps.json",
        checkpoint="checkpoints/NoobAI-XL-v1.1.safetensors",
        vae="vae/sdxl_vae.safetensors",
        vram_estimate_gb=7.0,
        prediction="eps",
        capabilities={"image_gen": True},
        defaults={
            "size": "1024x1024",
            "steps": 28,
            "cfg": 5.0,
            "sampler": "euler_ancestral",
            "scheduler": "karras",
            "negative_prompt": "worst quality, low quality",
        },
        limits={"steps_max": 60, "n_max": 4, "size_max_pixels": 1572864},
    )
    return Registry({cfg.name: cfg})


def _body(**overrides: Any) -> dict[str, Any]:
    base = {"model": "noobai-xl-v1.1", "prompt": "a cat"}
    base.update(overrides)
    return base


# ───────────────────────── Pydantic shape ─────────────────────────


def test_minimal_body_parses(registry: Registry) -> None:
    req = GenerateRequest.model_validate(_body())
    job = resolve_and_validate(req, registry=registry, async_mode_enabled=False)
    assert job.model.name == "noobai-xl-v1.1"
    assert job.prompt == "a cat"
    # defaults merged
    assert job.steps == 28
    assert job.sampler == "euler_ancestral"
    assert job.negative_prompt == "worst quality, low quality"


def test_prompt_empty_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(prompt=""))


def test_prompt_over_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(prompt="a" * 8001))


def test_prompt_exactly_8000_ok() -> None:
    GenerateRequest.model_validate(_body(prompt="a" * 8000))


def test_negative_prompt_over_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(negative_prompt="x" * 2001))


def test_size_malformed_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(size="1024"))


def test_cfg_below_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(cfg=-1))


def test_cfg_above_thirty_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(cfg=31))


def test_seed_below_minus_one_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(seed=-2))


def test_seed_above_max_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(seed=(2**53) + 1))


def test_response_format_invalid_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(response_format="raw"))


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(frobnicate=True))


def test_webhook_field_rejected() -> None:
    """Cycle 9 will enable; Cycle 3 rejects."""
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(webhook={"url": "https://x"}))


def test_loras_field_rejected() -> None:
    """Cycle 5 will enable; Cycle 3 rejects."""
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(loras=[{"name": "x", "weight": 0.5}]))


# ───────────────────────── post-Pydantic resolve ─────────────────────────


def test_unknown_model_raises(registry: Registry) -> None:
    req = GenerateRequest.model_validate(_body(model="no-such-model"))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False)
    assert exc.value.error_code == "validation_error"
    assert "model" in exc.value.message.lower()


def test_size_exceeds_max_pixels_rejected(registry: Registry) -> None:
    req = GenerateRequest.model_validate(_body(size="2048x2048"))  # 4M px > 1.5M
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False)
    assert exc.value.error_code == "validation_error"
    assert "size" in exc.value.message.lower()


def test_n_exceeds_max_rejected(registry: Registry) -> None:
    req = GenerateRequest.model_validate(_body(n=5))  # n_max=4
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False)
    assert exc.value.error_code == "validation_error"


def test_steps_exceeds_max_rejected(registry: Registry) -> None:
    req = GenerateRequest.model_validate(_body(steps=61))  # steps_max=60
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False)
    assert exc.value.error_code == "validation_error"


def test_sampler_not_in_enum_rejected(registry: Registry) -> None:
    req = GenerateRequest.model_validate(_body(sampler="bogus"))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False)
    assert exc.value.error_code == "validation_error"
    assert "sampler" in exc.value.message.lower()


def test_scheduler_not_in_enum_rejected(registry: Registry) -> None:
    req = GenerateRequest.model_validate(_body(scheduler="bogus"))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False)
    assert exc.value.error_code == "validation_error"
    assert "scheduler" in exc.value.message.lower()


def test_mode_async_rejected_when_flag_off(registry: Registry) -> None:
    req = GenerateRequest.model_validate(_body(mode="async"))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False)
    assert exc.value.error_code == "async_not_enabled"


def test_mode_async_allowed_when_flag_on(registry: Registry) -> None:
    req = GenerateRequest.model_validate(_body(mode="async"))
    job = resolve_and_validate(req, registry=registry, async_mode_enabled=True)
    assert job.mode == "async"


def test_allowed_sampler_scheduler_sets_sane_defaults() -> None:
    # Just a sanity check: the allowed enum sets are non-empty and contain our defaults.
    assert "euler_ancestral" in ALLOWED_SAMPLERS
    assert "karras" in ALLOWED_SCHEDULERS
