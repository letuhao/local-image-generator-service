"""WAN video: workflow anchors, LoRA injection, wan_advanced patches."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.base import ModelConfig
from app.registry.wan_advanced import apply_wan_advanced_patches
from app.registry.workflows import (
    ResolvedLoraRef,
    find_anchor,
    inject_video_weights,
    inject_wan_lora_multi,
    load_workflow,
    required_anchors_for_video_task,
    required_anchors_for_wan22_audio_task,
    validate_anchors,
)
from app.validation import (
    WanVideoAdvancedOptions,
    WanVideoEncodeOptions,
    WanVideoExportOptions,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("filename", "video_task"),
    [
        ("wan22_t2v_api.json", "t2v"),
        ("wan22_i2v_api.json", "i2v"),
    ],
)
def test_wan22_silent_api_anchor_set(filename: str, video_task: str) -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / filename)
    validate_anchors(graph, required_anchors_for_video_task(video_task))


@pytest.mark.parametrize(
    ("filename", "video_task"),
    [
        ("wan22_t2v_audio_api.json", "t2v"),
        ("wan22_i2v_audio_api.json", "i2v"),
    ],
)
def test_wan22_audio_api_anchor_set(filename: str, video_task: str) -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / filename)
    validate_anchors(graph, required_anchors_for_wan22_audio_task(video_task))


def _wan_loaders_wired_to_multi(graph: dict, multi_id: str) -> list[str]:
    out: list[str] = []
    for nid, nd in graph.items():
        if nd.get("class_type") != "WanVideoModelLoader":
            continue
        ins = nd.get("inputs") or {}
        wire = ins.get("lora")
        if isinstance(wire, list) and len(wire) >= 1 and str(wire[0]) == str(multi_id):
            out.append(nid)
    return out


def test_inject_video_weights_sets_wan_vae_precision_from_filename() -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    vae_id = find_anchor(graph, "%WAN_VAE%")
    assert graph[vae_id]["inputs"]["precision"] == "bf16"

    inject_video_weights(
        graph,
        model_cfg=ModelConfig(
            name="fixture-wan-vae",
            backend="comfyui",
            family="wan22",
            prediction="eps",
            workflow_path="workflows/wan22_i2v_api.json",
            checkpoint="diffusion_models/Z.safetensors",
            vae="vae/Wan2_1_VAE_fp32.safetensors",
            vram_estimate_gb=11.0,
            wan_t5_encoder="text_encoders/umt5.safetensors",
            wan_clip_vision="clip_vision/h.safetensors",
            capabilities={"video_gen": True, "video_task": "i2v"},
        ),
    )
    assert graph[vae_id]["inputs"]["model_name"] == "Wan2_1_VAE_fp32.safetensors"
    assert graph[vae_id]["inputs"]["precision"] == "fp32"

    graph2 = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    v2 = find_anchor(graph2, "%WAN_VAE%")
    inject_video_weights(
        graph2,
        model_cfg=ModelConfig(
            name="fixture-wan-vae-bf16",
            backend="comfyui",
            family="wan22",
            prediction="eps",
            workflow_path="workflows/wan22_i2v_api.json",
            checkpoint="diffusion_models/Z.safetensors",
            vae="vae/Wan2_1_VAE_bf16.safetensors",
            vram_estimate_gb=11.0,
            wan_t5_encoder="text_encoders/umt5.safetensors",
            wan_clip_vision="clip_vision/h.safetensors",
            capabilities={"video_gen": True, "video_task": "i2v"},
        ),
    )
    assert graph2[v2]["inputs"]["precision"] == "bf16"


def test_inject_wan_lora_multi_empty_disconnects_loader() -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_t2v_api.json")
    mid = find_anchor(graph, "%WAN_LORA_MULTI%")
    loaders = _wan_loaders_wired_to_multi(graph, mid)
    assert loaders, "fixture should connect WanVideoModelLoader.lora -> %WAN_LORA_MULTI%"
    inject_wan_lora_multi(graph, ())
    for lid in loaders:
        assert "lora" not in graph[lid]["inputs"]


def test_inject_wan_lora_multi_nonempty_keeps_loader_wire() -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    mid = find_anchor(graph, "%WAN_LORA_MULTI%")
    before = {
        lid: graph[lid]["inputs"]["lora"].copy()
        for lid in _wan_loaders_wired_to_multi(graph, mid)
    }
    assert before
    inject_wan_lora_multi(graph, (ResolvedLoraRef(name="a", weight=0.5),))
    for lid, prev in before.items():
        assert graph[lid]["inputs"]["lora"] == prev


def test_inject_wan_lora_multi_maps_order_and_flags() -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_t2v_api.json")
    inject_wan_lora_multi(
        graph,
        (
            ResolvedLoraRef(name="sub/a", weight=0.5),
            ResolvedLoraRef(name="b", weight=1.25),
        ),
        merge_loras=False,
        low_mem_load=True,
    )
    mid = find_anchor(graph, "%WAN_LORA_MULTI%")
    ins = graph[mid]["inputs"]
    assert ins["lora_0"] == "sub/a.safetensors"
    assert ins["strength_0"] == 0.5
    assert ins["lora_1"] == "b.safetensors"
    assert ins["strength_1"] == 1.25
    assert ins["lora_2"] == "none"
    assert ins["merge_loras"] is False
    assert ins["low_mem_load"] is True


def test_apply_wan_advanced_i2v_encode_and_export() -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    opts = WanVideoAdvancedOptions(
        encode=WanVideoEncodeOptions(noise_aug_strength=0.07),
        export=WanVideoExportOptions(crf=22, pix_fmt="yuv420p"),
    )
    apply_wan_advanced_patches(graph, opts, video_task="i2v")
    dims_id = find_anchor(graph, "%WAN_DIMS%")
    assert graph[dims_id]["inputs"]["noise_aug_strength"] == 0.07
    out_id = find_anchor(graph, "%VIDEO_OUTPUT%")
    assert graph[out_id]["inputs"]["crf"] == 22
    assert graph[out_id]["inputs"]["pix_fmt"] == "yuv420p"


def test_apply_wan_advanced_t2v_skips_encode() -> None:
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_t2v_api.json")
    opts = WanVideoAdvancedOptions(
        encode=WanVideoEncodeOptions(noise_aug_strength=0.99),
        export=WanVideoExportOptions(crf=21),
    )
    apply_wan_advanced_patches(graph, opts, video_task="t2v")
    dims_id = find_anchor(graph, "%WAN_DIMS%")
    dim_in = graph[dims_id]["inputs"]
    assert "noise_aug_strength" not in dim_in
    out_id = find_anchor(graph, "%VIDEO_OUTPUT%")
    assert graph[out_id]["inputs"]["crf"] == 21
