from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.backends.base import ModelConfig
from app.registry.models import (
    Registry,
    RegistryValidationError,
    load_registry,
)
from app.registry.workflows import inject_model_source, load_workflow

_BASE_YAML = {
    "models": [
        {
            "name": "noobai-xl-v1.1",
            "backend": "comfyui",
            "workflow": "workflows/sdxl_eps.json",
            "checkpoint": "checkpoints/NoobAI-XL-v1.1.safetensors",
            "prediction": "eps",
            "vae": "vae/sdxl_vae.safetensors",
            "capabilities": {"image_gen": True},
            "defaults": {
                "size": "1024x1024",
                "steps": 28,
                "cfg": 5.0,
                "sampler": "euler_ancestral",
                "scheduler": "karras",
                "negative_prompt": "worst quality, low quality",
            },
            "limits": {
                "steps_max": 60,
                "n_max": 4,
                "size_max_pixels": 1572864,
            },
            "vram_estimate_gb": 7,
        }
    ]
}


def _dump_yaml(tmp_path: Path, body: dict) -> Path:
    p = tmp_path / "models.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    return p


def _make_dummy_workflow(path: Path) -> None:
    """Write a minimal workflow with all required anchors so anchor validation passes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = {
        "1": {
            "class_type": "X",
            "inputs": {},
            "_meta": {"title": "%MODEL_SOURCE%,%CLIP_SOURCE%,%LORA_INSERT%"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ""},
            "_meta": {"title": "%POSITIVE_PROMPT%"},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ""},
            "_meta": {"title": "%NEGATIVE_PROMPT%"},
        },
        "4": {"class_type": "KSampler", "inputs": {}, "_meta": {"title": "%KSAMPLER%"}},
        "5": {"class_type": "SaveImage", "inputs": {}, "_meta": {"title": "%OUTPUT%"}},
    }
    path.write_text(json.dumps(graph), encoding="utf-8")


def _make_dummy_flux_workflow(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = {
        "1": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": "chroma.gguf"},
            "_meta": {"title": "%MODEL_SOURCE%,%LORA_INSERT%"},
        },
        "2": {
            "class_type": "DualCLIPLoader",
            "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl.safetensors"},
            "_meta": {"title": "%CLIP_SOURCE%"},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["2", 0]},
            "_meta": {"title": "%POSITIVE_PROMPT%"},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["2", 0]},
            "_meta": {"title": "%NEGATIVE_PROMPT%"},
        },
        "5": {"class_type": "KSampler", "inputs": {}, "_meta": {"title": "%KSAMPLER%"}},
        "6": {"class_type": "SaveImage", "inputs": {}, "_meta": {"title": "%OUTPUT%"}},
    }
    path.write_text(json.dumps(graph), encoding="utf-8")


def _make_dummy_flux2_clip_workflow(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = {
        "1": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": "placeholder.gguf"},
            "_meta": {"title": "%MODEL_SOURCE%,%LORA_INSERT%"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "mistral.safetensors", "type": "flux2"},
            "_meta": {"title": "%CLIP_SOURCE%"},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["2", 0]},
            "_meta": {"title": "%POSITIVE_PROMPT%"},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["2", 0]},
            "_meta": {"title": "%NEGATIVE_PROMPT%"},
        },
        "5": {"class_type": "KSampler", "inputs": {}, "_meta": {"title": "%KSAMPLER%"}},
        "6": {"class_type": "SaveImage", "inputs": {}, "_meta": {"title": "%OUTPUT%"}},
    }
    path.write_text(json.dumps(graph), encoding="utf-8")


def _scaffold(
    tmp_path: Path,
    *,
    include_ckpt: bool = True,
    include_vae: bool = True,
    include_workflow: bool = True,
) -> tuple[Path, Path, Path]:
    """Create the on-disk scaffolding to match the _BASE_YAML entry."""
    models_root = tmp_path / "models"
    workflows_root = tmp_path / "workflows"
    yaml_path = _dump_yaml(tmp_path, _BASE_YAML)

    if include_ckpt:
        ckpt = models_root / "checkpoints" / "NoobAI-XL-v1.1.safetensors"
        ckpt.parent.mkdir(parents=True, exist_ok=True)
        ckpt.write_bytes(b"fake-checkpoint")

    if include_vae:
        vae = models_root / "vae" / "sdxl_vae.safetensors"
        vae.parent.mkdir(parents=True, exist_ok=True)
        vae.write_bytes(b"fake-vae")

    if include_workflow:
        _make_dummy_workflow(workflows_root / "sdxl_eps.json")

    return yaml_path, models_root, workflows_root


def test_load_registry_happy_path(tmp_path: Path) -> None:
    yaml_path, models_root, workflows_root = _scaffold(tmp_path)
    registry = load_registry(
        yaml_path,
        models_root=models_root,
        workflows_root=workflows_root.parent,
        vram_budget_gb=12,
    )
    assert isinstance(registry, Registry)
    assert "noobai-xl-v1.1" in registry.names()
    cfg = registry.get("noobai-xl-v1.1")
    assert cfg.name == "noobai-xl-v1.1"
    assert cfg.prediction == "eps"
    assert cfg.capabilities == {"image_gen": True}
    assert cfg.limits["steps_max"] == 60


def test_missing_checkpoint_raises(tmp_path: Path) -> None:
    yaml_path, models_root, workflows_root = _scaffold(tmp_path, include_ckpt=False)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "checkpoint_missing"


def test_missing_vae_raises(tmp_path: Path) -> None:
    yaml_path, models_root, workflows_root = _scaffold(tmp_path, include_vae=False)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "vae_missing"


def test_missing_workflow_raises(tmp_path: Path) -> None:
    yaml_path, models_root, workflows_root = _scaffold(tmp_path, include_workflow=False)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "workflow_missing"


def test_workflow_missing_anchor_raises(tmp_path: Path) -> None:
    yaml_path, models_root, workflows_root = _scaffold(tmp_path)
    # Overwrite the workflow with one missing %KSAMPLER%.
    (workflows_root / "sdxl_eps.json").write_text(
        json.dumps(
            {
                "1": {
                    "class_type": "X",
                    "inputs": {},
                    "_meta": {"title": "%MODEL_SOURCE%,%CLIP_SOURCE%,%LORA_INSERT%"},
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "anchors_missing"


def test_vram_over_budget_raises(tmp_path: Path) -> None:
    yaml_path, models_root, workflows_root = _scaffold(tmp_path)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=4,
        )
    assert exc.value.stage == "vram_over_budget"


def test_empty_registry_raises(tmp_path: Path) -> None:
    yaml_path = _dump_yaml(tmp_path, {"models": []})
    models_root = tmp_path / "models"
    workflows_root = tmp_path / "workflows"
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "empty_registry"


def test_registry_get_unknown_raises_key_error(tmp_path: Path) -> None:
    yaml_path, models_root, workflows_root = _scaffold(tmp_path)
    registry = load_registry(
        yaml_path, models_root=models_root, workflows_root=workflows_root.parent, vram_budget_gb=12
    )
    with pytest.raises(KeyError):
        registry.get("no-such-model")


def _scaffold_with_yaml_override(tmp_path: Path, body: dict) -> tuple[Path, Path, Path]:
    """Scaffold on-disk files (ckpt, vae, workflow) BUT dump a custom YAML body."""
    _, models_root, workflows_root = _scaffold(tmp_path)
    yaml_path = _dump_yaml(tmp_path, body)  # overwrite with custom body
    return yaml_path, models_root, workflows_root


def test_duplicate_model_name_raises(tmp_path: Path) -> None:
    """Two entries with the same `name` fail-fast at load, not silently overwrite."""
    import copy as _copy

    body = _copy.deepcopy(_BASE_YAML)
    body["models"].append(_copy.deepcopy(body["models"][0]))
    yaml_path, models_root, workflows_root = _scaffold_with_yaml_override(tmp_path, body)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "duplicate_name"


def test_unknown_backend_raises(tmp_path: Path) -> None:
    import copy as _copy

    body = _copy.deepcopy(_BASE_YAML)
    body["models"][0]["backend"] = "local"
    yaml_path, models_root, workflows_root = _scaffold_with_yaml_override(tmp_path, body)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "unknown_backend"


def test_unknown_prediction_raises(tmp_path: Path) -> None:
    import copy as _copy

    body = _copy.deepcopy(_BASE_YAML)
    body["models"][0]["prediction"] = "flow"
    yaml_path, models_root, workflows_root = _scaffold_with_yaml_override(tmp_path, body)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "unknown_prediction"


def test_unknown_family_raises(tmp_path: Path) -> None:
    import copy as _copy

    body = _copy.deepcopy(_BASE_YAML)
    body["models"][0]["family"] = "mystery"
    yaml_path, models_root, workflows_root = _scaffold_with_yaml_override(tmp_path, body)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "unknown_family"


def test_flux_family_workflow_anchor_validation(tmp_path: Path) -> None:
    import copy as _copy

    body = _copy.deepcopy(_BASE_YAML)
    body["models"][0]["family"] = "flux"
    body["models"][0]["workflow"] = "workflows/flux_mock.json"
    yaml_path, models_root, workflows_root = _scaffold_with_yaml_override(tmp_path, body)
    _make_dummy_flux_workflow(workflows_root / "flux_mock.json")
    registry = load_registry(
        yaml_path,
        models_root=models_root,
        workflows_root=workflows_root.parent,
        vram_budget_gb=12,
    )
    assert registry.get("noobai-xl-v1.1").family == "flux"


def test_vpred_prediction_refused_at_boot(tmp_path: Path) -> None:
    """Arch v0.5 defers v-prediction injection; a YAML with prediction='vpred'
    must fail to load so a future model bump can't land undetected."""
    import copy as _copy

    body = _copy.deepcopy(_BASE_YAML)
    body["models"][0]["prediction"] = "vpred"
    yaml_path, models_root, workflows_root = _scaffold_with_yaml_override(tmp_path, body)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "vpred_deferred"


def test_unknown_default_sampler_raises(tmp_path: Path) -> None:
    import copy as _copy

    body = _copy.deepcopy(_BASE_YAML)
    body["models"][0]["defaults"]["sampler"] = "nonexistent_sampler"
    yaml_path, models_root, workflows_root = _scaffold_with_yaml_override(tmp_path, body)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "unknown_sampler"


def test_unknown_default_scheduler_raises(tmp_path: Path) -> None:
    import copy as _copy

    body = _copy.deepcopy(_BASE_YAML)
    body["models"][0]["defaults"]["scheduler"] = "nonexistent_scheduler"
    yaml_path, models_root, workflows_root = _scaffold_with_yaml_override(tmp_path, body)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "unknown_scheduler"


def test_missing_clip_l_raises(tmp_path: Path) -> None:
    import copy as _copy

    body = _copy.deepcopy(_BASE_YAML)
    body["models"][0]["clip_l"] = "text_encoders/clip_l.safetensors"
    yaml_path, models_root, workflows_root = _scaffold_with_yaml_override(tmp_path, body)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "clip_l_missing"


def test_load_registry_flux2_clip_loader_happy_path(tmp_path: Path) -> None:
    yaml_path = tmp_path / "models.yaml"
    models_root = tmp_path / "models"
    workflows_root = tmp_path / "workflows"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "models": [
                    {
                        "name": "flux2-fixture",
                        "backend": "comfyui",
                        "family": "flux",
                        "workflow": "workflows/flux2_fixture.json",
                        "checkpoint": "unet/fixture.gguf",
                        "prediction": "eps",
                        "vae": "vae/flux2-vae.safetensors",
                        "clip_l": "text_encoders/mistral_fp8.safetensors",
                        "clip_loader_type": "flux2",
                        "capabilities": {"image_gen": True},
                        "defaults": {
                            "size": "1024x1024",
                            "steps": 28,
                            "cfg": 1.0,
                            "sampler": "euler",
                            "scheduler": "simple",
                            "negative_prompt": "",
                        },
                        "limits": {
                            "steps_max": 60,
                            "n_max": 2,
                            "size_max_pixels": 1572864,
                        },
                        "vram_estimate_gb": 14,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (models_root / "unet").mkdir(parents=True)
    (models_root / "unet" / "fixture.gguf").write_bytes(b"gg")
    (models_root / "vae").mkdir(parents=True)
    (models_root / "vae" / "flux2-vae.safetensors").write_bytes(b"v")
    (models_root / "text_encoders").mkdir(parents=True)
    (models_root / "text_encoders" / "mistral_fp8.safetensors").write_bytes(b"t")
    _make_dummy_flux2_clip_workflow(workflows_root / "flux2_fixture.json")

    registry = load_registry(
        yaml_path,
        models_root=models_root,
        workflows_root=tmp_path,
        vram_budget_gb=24,
    )
    cfg = registry.get("flux2-fixture")
    assert cfg.clip_loader_type == "flux2"
    assert cfg.t5xxl is None


def test_inject_model_source_clip_loader(tmp_path: Path) -> None:
    wf_path = tmp_path / "flux2_fixture.json"
    _make_dummy_flux2_clip_workflow(wf_path)
    graph = load_workflow(wf_path)
    cfg = ModelConfig(
        name="flux2-fixture",
        backend="comfyui",
        family="flux",
        workflow_path="workflows/flux2_fixture.json",
        checkpoint="unet/fixture.gguf",
        vae="vae/flux2-vae.safetensors",
        clip_l="text_encoders/mistral_fp8.safetensors",
        clip_loader_type="flux2",
        vram_estimate_gb=14,
        prediction="eps",
        capabilities={"image_gen": True},
        defaults={
            "size": "1024x1024",
            "steps": 28,
            "cfg": 1.0,
            "sampler": "euler",
            "scheduler": "simple",
            "negative_prompt": "",
        },
        limits={"steps_max": 60, "n_max": 2, "size_max_pixels": 1572864},
    )
    inject_model_source(graph, model_cfg=cfg)
    assert graph["1"]["inputs"]["unet_name"] == "fixture.gguf"
    assert graph["2"]["inputs"]["clip_name"] == "mistral_fp8.safetensors"
    assert graph["2"]["inputs"]["type"] == "flux2"


def test_missing_t5xxl_raises(tmp_path: Path) -> None:
    import copy as _copy

    body = _copy.deepcopy(_BASE_YAML)
    body["models"][0]["t5xxl"] = "text_encoders/t5xxl_fp8_e4m3fn.safetensors"
    yaml_path, models_root, workflows_root = _scaffold_with_yaml_override(tmp_path, body)
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=workflows_root.parent,
            vram_budget_gb=12,
        )
    assert exc.value.stage == "t5xxl_missing"
