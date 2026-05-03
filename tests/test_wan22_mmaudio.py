"""WAN optional-audio graphs: anchors + MMAudio injection."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.backends.base import ModelConfig
from app.registry.models import RegistryValidationError, load_registry
from app.registry.workflows import (
    inject_mmaudio_weights,
    load_workflow,
    required_anchors_for_wan22_audio_task,
    validate_anchors,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wan22_t2v_audio_api_anchor_validation() -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_t2v_audio_api.json")
    validate_anchors(graph, required_anchors_for_wan22_audio_task("t2v"))


def test_wan22_i2v_audio_api_anchor_validation() -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_audio_api.json")
    validate_anchors(graph, required_anchors_for_wan22_audio_task("i2v"))


def test_inject_mmaudio_weights_updates_basenames() -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_t2v_audio_api.json")
    cfg = ModelConfig(
        name="mmaudio-fixture",
        backend="comfyui",
        family="wan22",
        workflow_path="workflows/wan22_t2v_audio_api.json",
        checkpoint="diffusion_models/x.safetensors",
        vae="vae/y.safetensors",
        vram_estimate_gb=10.0,
        wan_t5_encoder="text_encoders/t5.safetensors",
        mmaudio_vae="mmaudio/custom_vae.safetensors",
        mmaudio_synchformer="mmaudio/custom_sync.safetensors",
        mmaudio_clip="mmaudio/custom_clip.safetensors",
        mmaudio_diffusion="mmaudio/custom_main.safetensors",
        capabilities={"video_gen": True, "video_task": "t2v"},
        defaults={"steps": 30},
        limits={"steps_max": 60, "frames_max": 129, "size_max_pixels": 921600},
    )
    inject_mmaudio_weights(graph, model_cfg=cfg)
    fu_id = next(
        nid
        for nid, node in graph.items()
        if node.get("class_type") == "MMAudioFeatureUtilsLoader"
    )
    md_id = next(
        nid for nid, node in graph.items() if node.get("class_type") == "MMAudioModelLoader"
    )
    assert graph[fu_id]["inputs"]["vae_model"] == "custom_vae.safetensors"
    assert graph[fu_id]["inputs"]["synchformer_model"] == "custom_sync.safetensors"
    assert graph[fu_id]["inputs"]["clip_model"] == "custom_clip.safetensors"
    assert graph[md_id]["inputs"]["mmaudio_model"] == "custom_main.safetensors"


def test_registry_rejects_audio_workflow_without_mmaudio_paths(tmp_path: Path) -> None:
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()

    shutil.copy(REPO_ROOT / "workflows" / "wan22_t2v_audio_api.json", wf_dir / "wan22_t2v_audio_api.json")
    shutil.copy(REPO_ROOT / "workflows" / "wan22_t2v_api.json", wf_dir / "wan22_t2v_api.json")

    yaml_path = tmp_path / "models.yaml"
    yaml_path.write_text(
        """
models:
  - name: wan-incomplete-audio
    backend: comfyui
    family: wan22
    prediction: eps
    workflow: workflows/wan22_t2v_api.json
    workflow_with_audio: workflows/wan22_t2v_audio_api.json
    checkpoint: diffusion_models/x.safetensors
    vae: vae/y.safetensors
    wan_t5_encoder: text_encoders/t5.safetensors
    skip_asset_validation: true
    capabilities:
      video_gen: true
      video_task: t2v
    defaults:
      size: "832x480"
      steps: 30
      cfg: 5.0
      scheduler: unipc
      negative_prompt: ""
    limits:
      steps_max: 60
      frames_max: 129
      size_max_pixels: 921600
    vram_estimate_gb: 10
""".strip(),
        encoding="utf-8",
    )

    models_root = tmp_path / "models"
    models_root.mkdir()
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(
            yaml_path,
            models_root=models_root,
            workflows_root=tmp_path,
            vram_budget_gb=24,
        )
    assert exc.value.stage == "mmaudio_config_incomplete"


def test_registry_loads_wan22_with_audio_workflows(tmp_path: Path) -> None:
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()

    for name in (
        "wan22_t2v_api.json",
        "wan22_t2v_audio_api.json",
        "wan22_i2v_api.json",
        "wan22_i2v_audio_api.json",
    ):
        shutil.copy(REPO_ROOT / "workflows" / name, wf_dir / name)

    yaml_path = tmp_path / "models.yaml"
    yaml_path.write_text(
        """
models:
  - name: wan-t2v-audio-reg
    backend: comfyui
    family: wan22
    prediction: eps
    workflow: workflows/wan22_t2v_api.json
    workflow_with_audio: workflows/wan22_t2v_audio_api.json
    mmaudio_vae: mmaudio/a.safetensors
    mmaudio_synchformer: mmaudio/b.safetensors
    mmaudio_clip: mmaudio/c.safetensors
    mmaudio_diffusion: mmaudio/d.safetensors
    checkpoint: diffusion_models/x.safetensors
    vae: vae/y.safetensors
    wan_t5_encoder: text_encoders/t5.safetensors
    skip_asset_validation: true
    capabilities:
      video_gen: true
      video_task: t2v
    defaults:
      size: "832x480"
      steps: 30
      cfg: 5.0
      scheduler: unipc
      negative_prompt: ""
    limits:
      steps_max: 60
      frames_max: 129
      size_max_pixels: 921600
    vram_estimate_gb: 10
""".strip(),
        encoding="utf-8",
    )

    models_root = tmp_path / "models"
    models_root.mkdir()
    reg = load_registry(
        yaml_path,
        models_root=models_root,
        workflows_root=tmp_path,
        vram_budget_gb=24,
    )
    cfg = reg.get("wan-t2v-audio-reg")
    assert cfg.workflow_with_audio == "workflows/wan22_t2v_audio_api.json"
    assert cfg.mmaudio_vae == "mmaudio/a.safetensors"
