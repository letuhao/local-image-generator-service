from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.registry.models import (
    Registry,
    RegistryValidationError,
    load_registry,
)

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
