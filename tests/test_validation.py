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
    VideoGenerateRequest,
    ValidationFailureError,
    _normalize_wan_num_frames,
    _touch_last_used_sync,
    resolve_and_validate,
    resolve_and_validate_video,
    touch_last_used_async,
)

_MINI_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _wan22_t2v_model_cfg(**overrides: Any) -> ModelConfig:
    base = dict(
        name="wan22-t2v-fixture",
        backend="comfyui",
        family="wan22",
        workflow_path="workflows/wan22_t2v_api.json",
        checkpoint="diffusion_models/x.safetensors",
        vae="vae/y.safetensors",
        wan_t5_encoder="text_encoders/t5.safetensors",
        vram_estimate_gb=10.0,
        prediction="eps",
        capabilities={"video_gen": True, "video_task": "t2v"},
        defaults={
            "size": "832x480",
            "steps": 30,
            "cfg": 5.0,
            "shift": 5.0,
            "scheduler": "unipc",
            "negative_prompt": "",
            "mmaudio_steps": 25,
            "mmaudio_cfg": 4.5,
        },
        limits={"steps_max": 60, "frames_max": 129, "size_max_pixels": 921600},
    )
    base.update(overrides)
    return ModelConfig(**base)


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


@pytest.fixture
def registry_with_flux(registry: Registry) -> Registry:
    flux_cfg = ModelConfig(
        name="flux1-dev",
        backend="comfyui",
        family="flux",
        workflow_path="workflows/flux_checkpoint.json",
        checkpoint="checkpoints/flux1-dev.safetensors",
        vae=None,
        vram_estimate_gb=12.0,
        prediction="eps",
        capabilities={"image_gen": True},
        defaults={
            "size": "1024x1024",
            "steps": 28,
            "cfg": 4.0,
            "sampler": "euler",
            "scheduler": "simple",
            "negative_prompt": "",
        },
        limits={"steps_max": 50, "n_max": 2, "size_max_pixels": 1572864},
    )
    existing = {cfg.name: cfg for cfg in registry.all()}
    existing[flux_cfg.name] = flux_cfg
    return Registry(existing)


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


def test_timeout_s_below_min_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(timeout_s=0.5))


def test_timeout_s_above_max_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(timeout_s=3601))


def test_timeout_s_parses_and_resolves(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(timeout_s=123.0))
    job = resolve_and_validate(
        req, registry=registry, async_mode_enabled=False, loras_root=tmp_path
    )
    assert job.timeout_s == pytest.approx(123.0)


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(_body(frobnicate=True))


def test_webhook_field_accepted() -> None:
    req = GenerateRequest.model_validate(
        _body(webhook={"url": "https://receiver.example/webhooks/image-gen"})
    )
    assert req.webhook is not None
    assert req.webhook.url.startswith("https://")


def test_loras_field_accepted() -> None:
    """Cycle 5 enables loras on GenerateRequest."""
    req = GenerateRequest.model_validate(_body(loras=[{"name": "x", "weight": 0.5}]))
    assert req.loras is not None
    assert req.loras[0].name == "x"
    assert req.loras[0].weight == 0.5



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


def test_flux_family_rejects_scheduler_outside_flux_profile(
    registry_with_flux: Registry, tmp_path: Any
) -> None:
    req = GenerateRequest.model_validate(_body(model="flux1-dev", scheduler="ddim_uniform"))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(
            req, registry=registry_with_flux, async_mode_enabled=False, loras_root=tmp_path
        )
    assert exc.value.error_code == "validation_error"
    assert "family='flux'" in exc.value.message


def test_universal_payload_parity_for_sdxl_and_flux(
    registry_with_flux: Registry, tmp_path: Any
) -> None:
    payload = {
        "prompt": "single game prop",
        "size": "1024x1024",
        "steps": 24,
        "cfg": 4.2,
        "seed": 101,
        "sampler": "euler",
        "scheduler": "simple",
        "mode": "sync",
        "transparent_background": True,
    }
    sdxl_req = GenerateRequest.model_validate({"model": "noobai-xl-v1.1", **payload})
    flux_req = GenerateRequest.model_validate({"model": "flux1-dev", **payload})
    sdxl_job = resolve_and_validate(
        sdxl_req, registry=registry_with_flux, async_mode_enabled=False, loras_root=tmp_path
    )
    flux_job = resolve_and_validate(
        flux_req, registry=registry_with_flux, async_mode_enabled=False, loras_root=tmp_path
    )
    assert sdxl_job.model.family == "sdxl"
    assert flux_job.model.family == "flux"
    assert sdxl_job.prompt == flux_job.prompt


def test_mode_async_rejected_when_flag_off(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(mode="async"))
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False, loras_root=tmp_path)
    assert exc.value.error_code == "async_not_enabled"


def test_mode_async_allowed_when_flag_on(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(_body(mode="async"))
    job = resolve_and_validate(req, registry=registry, async_mode_enabled=True, loras_root=tmp_path)
    assert job.mode == "async"


def test_webhook_resolves_into_validated_job(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(
        _body(
            mode="async",
            webhook={
                "url": "https://receiver.example/v1/webhooks/image-gen",
                "headers": {"X-Tenant": "tenant-a"},
            },
        )
    )
    job = resolve_and_validate(req, registry=registry, async_mode_enabled=True, loras_root=tmp_path)
    assert job.webhook_url == "https://receiver.example/v1/webhooks/image-gen"
    assert job.webhook_headers == {"X-Tenant": "tenant-a"}


def test_webhook_reserved_header_rejected(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(
        _body(
            mode="async",
            webhook={
                "url": "https://receiver.example/v1/webhooks/image-gen",
                "headers": {"Authorization": "Bearer nope"},
            },
        )
    )
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=True, loras_root=tmp_path)
    assert exc.value.error_code == "validation_error"
    assert "reserved" in exc.value.message


def test_webhook_header_invalid_key_rejected(registry: Registry, tmp_path: Any) -> None:
    req = GenerateRequest.model_validate(
        _body(
            mode="async",
            webhook={
                "url": "https://receiver.example/v1/webhooks/image-gen",
                "headers": {"Bad Header": "x"},
            },
        )
    )
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=True, loras_root=tmp_path)
    assert exc.value.error_code == "validation_error"
    assert "invalid characters" in exc.value.message


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


def test_vram_guard_exceeded_by_lora_count(
    registry: Registry, tmp_path: Any, monkeypatch: Any
) -> None:
    """7.0 + (20 * 0.064) > 8.0, so guard should reject."""
    monkeypatch.setenv("VRAM_BUDGET_GB", "8")
    for i in range(20):
        (tmp_path / f"l{i}.safetensors").write_bytes(b"\x00")
    req = GenerateRequest.model_validate(
        _body(loras=[{"name": f"l{i}", "weight": 0.1} for i in range(20)])
    )
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(req, registry=registry, async_mode_enabled=False, loras_root=tmp_path)
    assert exc.value.error_code == "vram_budget_exceeded"


def test_vram_guard_respects_custom_budget(
    registry: Registry, tmp_path: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("VRAM_BUDGET_GB", "7.01")
    req = GenerateRequest.model_validate(_body())
    job = resolve_and_validate(
        req, registry=registry, async_mode_enabled=False, loras_root=tmp_path
    )
    assert job.model.name == "noobai-xl-v1.1"


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


def test_normalize_wan_num_frames_rounds_up_not_down() -> None:
    """WAN temporal length must satisfy n ≡ 1 (mod 4); never shrink 8 → 5."""
    assert _normalize_wan_num_frames(4) == 5
    assert _normalize_wan_num_frames(5) == 5
    assert _normalize_wan_num_frames(8) == 9
    assert _normalize_wan_num_frames(9) == 9
    assert _normalize_wan_num_frames(81) == 81


def test_video_generate_audio_requires_workflow_with_audio(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("VRAM_BUDGET_GB", "24")
    cfg = _wan22_t2v_model_cfg(workflow_with_audio=None)
    reg = Registry({cfg.name: cfg})
    req = VideoGenerateRequest.model_validate(
        {"model": cfg.name, "prompt": "ocean waves", "generate_audio": True}
    )
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate_video(
            req,
            registry=reg,
            async_mode_enabled=False,
            expected_task="t2v",
            loras_root=tmp_path,
        )
    assert "workflow_with_audio" in exc.value.message


def test_video_generate_audio_selects_audio_workflow(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("VRAM_BUDGET_GB", "24")
    cfg = _wan22_t2v_model_cfg(
        workflow_with_audio="workflows/wan22_t2v_audio_api.json",
        mmaudio_vae="mmaudio/a.safetensors",
        mmaudio_synchformer="mmaudio/b.safetensors",
        mmaudio_clip="mmaudio/c.safetensors",
        mmaudio_diffusion="mmaudio/d.safetensors",
    )
    reg = Registry({cfg.name: cfg})
    req = VideoGenerateRequest.model_validate(
        {"model": cfg.name, "prompt": "soft rain ambience", "generate_audio": True}
    )
    job = resolve_and_validate_video(
        req,
        registry=reg,
        async_mode_enabled=False,
        expected_task="t2v",
        loras_root=tmp_path,
    )
    assert job.generate_audio is True
    assert job.resolved_workflow_path == "workflows/wan22_t2v_audio_api.json"


def test_video_silent_uses_base_workflow(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("VRAM_BUDGET_GB", "24")
    cfg = _wan22_t2v_model_cfg(
        workflow_with_audio="workflows/wan22_t2v_audio_api.json",
        mmaudio_vae="mmaudio/a.safetensors",
        mmaudio_synchformer="mmaudio/b.safetensors",
        mmaudio_clip="mmaudio/c.safetensors",
        mmaudio_diffusion="mmaudio/d.safetensors",
    )
    reg = Registry({cfg.name: cfg})
    req = VideoGenerateRequest.model_validate({"model": cfg.name, "prompt": "silent clip"})
    job = resolve_and_validate_video(
        req,
        registry=reg,
        async_mode_enabled=False,
        expected_task="t2v",
        loras_root=tmp_path,
    )
    assert job.generate_audio is False
    assert job.resolved_workflow_path == "workflows/wan22_t2v_api.json"


def test_video_i2v_generate_audio_ok(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("VRAM_BUDGET_GB", "24")
    cfg = ModelConfig(
        name="wan22-i2v-fixture",
        backend="comfyui",
        family="wan22",
        workflow_path="workflows/wan22_i2v_api.json",
        workflow_with_audio="workflows/wan22_i2v_audio_api.json",
        checkpoint="diffusion_models/x.safetensors",
        vae="vae/y.safetensors",
        wan_t5_encoder="text_encoders/t5.safetensors",
        wan_clip_vision="clip_vision/h.safetensors",
        mmaudio_vae="mmaudio/a.safetensors",
        mmaudio_synchformer="mmaudio/b.safetensors",
        mmaudio_clip="mmaudio/c.safetensors",
        mmaudio_diffusion="mmaudio/d.safetensors",
        vram_estimate_gb=10.0,
        prediction="eps",
        capabilities={"video_gen": True, "video_task": "i2v"},
        defaults={
            "size": "832x480",
            "steps": 30,
            "cfg": 5.0,
            "shift": 5.0,
            "scheduler": "unipc",
            "negative_prompt": "",
        },
        limits={"steps_max": 60, "frames_max": 129, "size_max_pixels": 921600},
    )
    reg = Registry({cfg.name: cfg})
    req = VideoGenerateRequest.model_validate(
        {
            "model": cfg.name,
            "prompt": "motion",
            "init_image": _MINI_PNG_B64,
            "generate_audio": True,
        }
    )
    job = resolve_and_validate_video(
        req,
        registry=reg,
        async_mode_enabled=False,
        expected_task="i2v",
        loras_root=tmp_path,
    )
    assert job.resolved_workflow_path == "workflows/wan22_i2v_audio_api.json"


def test_video_t2v_rejects_wan_advanced_encode(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("VRAM_BUDGET_GB", "24")
    cfg = _wan22_t2v_model_cfg()
    reg = Registry({cfg.name: cfg})
    req = VideoGenerateRequest.model_validate(
        {
            "model": cfg.name,
            "prompt": "motion",
            "wan_advanced": {"encode": {"noise_aug_strength": 0.1}},
        }
    )
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate_video(
            req,
            registry=reg,
            async_mode_enabled=False,
            expected_task="t2v",
            loras_root=tmp_path,
        )
    assert "image-to-video only" in exc.value.message


def test_video_i2v_resolves_wan_loras(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("VRAM_BUDGET_GB", "24")
    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    (loras_root / "style_x.safetensors").write_bytes(b"x")
    cfg = ModelConfig(
        name="wan22-i2v-lora",
        backend="comfyui",
        family="wan22",
        workflow_path="workflows/wan22_i2v_api.json",
        checkpoint="diffusion_models/x.safetensors",
        vae="vae/y.safetensors",
        wan_t5_encoder="text_encoders/t5.safetensors",
        wan_clip_vision="clip_vision/h.safetensors",
        vram_estimate_gb=10.0,
        prediction="eps",
        capabilities={"video_gen": True, "video_task": "i2v"},
        defaults={
            "size": "832x480",
            "steps": 30,
            "cfg": 5.0,
            "shift": 5.0,
            "scheduler": "unipc",
            "negative_prompt": "",
        },
        limits={"steps_max": 60, "frames_max": 129, "size_max_pixels": 921600},
    )
    reg = Registry({cfg.name: cfg})
    req = VideoGenerateRequest.model_validate(
        {
            "model": cfg.name,
            "prompt": "motion",
            "init_image": _MINI_PNG_B64,
            "loras": [{"name": "style_x", "weight": 1.15}],
        }
    )
    job = resolve_and_validate_video(
        req,
        registry=reg,
        async_mode_enabled=False,
        expected_task="i2v",
        loras_root=loras_root,
    )
    assert len(job.loras) == 1
    assert job.loras[0].name == "style_x"
    assert job.loras[0].weight == pytest.approx(1.15)


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
