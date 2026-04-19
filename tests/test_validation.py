from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.backends.base import ModelConfig
from app.registry.models import Registry
from app.registry.workflows import ResolvedLoraRef
from app.validation import (
    ALLOWED_SAMPLERS,
    ALLOWED_SCHEDULERS,
    GenerateRequest,
    ValidationFailureError,
    _touch_last_used_sync,
    resolve_and_validate,
    touch_last_used_async,
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


def test_minimal_body_parses(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body())
    job = resolve_and_validate(
        req, registry=registry, async_mode_enabled=False, loras_root=tmp_path
    )
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


def test_loras_field_accepted() -> None:
    """Cycle 5 enables loras on GenerateRequest."""
    req = GenerateRequest.model_validate(_body(loras=[{"name": "x", "weight": 0.5}]))
    assert req.loras is not None
    assert req.loras[0].name == "x"
    assert req.loras[0].weight == 0.5


def test_loras_weight_out_of_bounds_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(loras=[{"name": "x", "weight": 3.0}]))


def test_loras_name_with_bad_chars_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(loras=[{"name": "../escape", "weight": 0.5}]))


def test_loras_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(loras=[{"name": "x", "weight": 0.5, "junk": 1}]))


def test_loras_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(
            _body(loras=[{"name": f"n{i}", "weight": 0.1} for i in range(21)])
        )


# ───────────────────────── post-Pydantic resolve ─────────────────────────


def test_unknown_model_raises(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(model="no-such-model"))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False, loras_root=tmp_path)
    assert exc.value.error_code == "validation_error"
    assert "model" in exc.value.message.lower()


def test_size_exceeds_max_pixels_rejected(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(size="2048x2048"))  # 4M px > 1.5M
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False, loras_root=tmp_path)
    assert exc.value.error_code == "validation_error"
    assert "size" in exc.value.message.lower()


def test_n_exceeds_max_rejected(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(n=5))  # n_max=4
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False, loras_root=tmp_path)
    assert exc.value.error_code == "validation_error"


def test_steps_exceeds_max_rejected(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(steps=61))  # steps_max=60
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False, loras_root=tmp_path)
    assert exc.value.error_code == "validation_error"


def test_sampler_not_in_enum_rejected(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(sampler="bogus"))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False, loras_root=tmp_path)
    assert exc.value.error_code == "validation_error"
    assert "sampler" in exc.value.message.lower()


def test_scheduler_not_in_enum_rejected(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(scheduler="bogus"))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False, loras_root=tmp_path)
    assert exc.value.error_code == "validation_error"
    assert "scheduler" in exc.value.message.lower()


def test_mode_async_rejected_when_flag_off(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(mode="async"))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False, loras_root=tmp_path)
    assert exc.value.error_code == "async_not_enabled"


def test_mode_async_allowed_when_flag_on(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(mode="async"))
    job = resolve_and_validate(req, registry=registry, async_mode_enabled=True, loras_root=tmp_path)
    assert job.mode == "async"


def test_allowed_sampler_scheduler_sets_sane_defaults() -> None:
    # Just a sanity check: the allowed enum sets are non-empty and contain our defaults.
    assert "euler_ancestral" in ALLOWED_SAMPLERS
    assert "karras" in ALLOWED_SCHEDULERS


# ───────────────────────── LoRA resolve ─────────────────────────


def test_loras_resolve_ok(registry: Registry, tmp_path: Any) -> None:
    (tmp_path / "foo.safetensors").write_bytes(b"\x00")
    req = GenerateRequest.model_validate(_body(loras=[{"name": "foo", "weight": 0.5}]))
    job = resolve_and_validate(
        req, registry=registry, async_mode_enabled=False, loras_root=tmp_path
    )
    assert len(job.loras) == 1
    assert job.loras[0].name == "foo"
    assert job.loras[0].weight == 0.5


def test_loras_missing_file_raises_lora_missing(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(loras=[{"name": "nonexistent", "weight": 0.5}]))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False, loras_root=tmp_path)
    assert exc.value.error_code == "lora_missing"


def test_loras_subdir_name_ok(registry: Registry, tmp_path: Any) -> None:
    (tmp_path / "hanfu").mkdir()
    (tmp_path / "hanfu" / "Bai_LingMiao.safetensors").write_bytes(b"\x00")
    req = GenerateRequest.model_validate(
        _body(loras=[{"name": "hanfu/Bai_LingMiao", "weight": 0.8}])
    )
    job = resolve_and_validate(
        req, registry=registry, async_mode_enabled=False, loras_root=tmp_path
    )
    assert job.loras[0].name == "hanfu/Bai_LingMiao"


def test_loras_empty_list_ok(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(loras=[]))
    job = resolve_and_validate(
        req, registry=registry, async_mode_enabled=False, loras_root=tmp_path
    )
    assert job.loras == ()


# ─── Sidecar last_used debounce (Cycle 6) ─────────────────────────


def test_touch_sync_writes_when_stale(tmp_path: Path) -> None:
    """Sidecar with last_used 10 min ago → rewritten."""
    sidecar = tmp_path / "foo.json"
    stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    sidecar.write_text(json.dumps({"last_used": stale}), encoding="utf-8")

    _touch_last_used_sync(sidecar)

    data = json.loads(sidecar.read_text(encoding="utf-8"))
    new_ts = datetime.fromisoformat(data["last_used"])
    assert (datetime.now(UTC) - new_ts).total_seconds() < 5


def test_touch_sync_skips_when_fresh(tmp_path: Path, monkeypatch: Any) -> None:
    """Sidecar touched 2 min ago + 5-min debounce → left alone."""
    monkeypatch.setenv("LORA_LAST_USED_DEBOUNCE_S", "300")
    sidecar = tmp_path / "foo.json"
    fresh = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    payload = json.dumps({"last_used": fresh})
    sidecar.write_text(payload, encoding="utf-8")
    mtime_before = sidecar.stat().st_mtime_ns

    _touch_last_used_sync(sidecar)

    assert sidecar.read_text(encoding="utf-8") == payload
    assert sidecar.stat().st_mtime_ns == mtime_before


def test_touch_sync_missing_sidecar_noops(tmp_path: Path) -> None:
    _touch_last_used_sync(tmp_path / "absent.json")
    # No exception = pass.


async def test_touch_async_touches_every_lora(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps({}), encoding="utf-8")
    (tmp_path / "a.safetensors").write_bytes(b"")
    (tmp_path / "b.json").write_text(json.dumps({}), encoding="utf-8")
    (tmp_path / "b.safetensors").write_bytes(b"")
    resolved = (
        ResolvedLoraRef(name="a", weight=0.5),
        ResolvedLoraRef(name="b", weight=0.5),
    )
    await touch_last_used_async(tmp_path, resolved)
    assert "last_used" in json.loads((tmp_path / "a.json").read_text())
    assert "last_used" in json.loads((tmp_path / "b.json").read_text())
