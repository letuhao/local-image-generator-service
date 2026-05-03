"""WAN video: workflow anchors, LoRA injection, wan_advanced patches.

Tests updated for Hướng B (core ComfyUI nodes: UNETLoader / WanImageToVideo /
KSamplerAdvanced).  Legacy Kijai-wrapper tests that exercised %WAN_LORA_MULTI%
and WanVideoImageToVideoEncode are replaced with equivalent tests for the new
LoraLoaderModelOnly chain and the graceful-skip behaviour in advanced patches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.base import ModelConfig
from app.registry.wan_advanced import apply_wan_advanced_patches
from app.registry.workflows import (
    ResolvedLoraRef,
    find_anchor,
    inject_video_weights,
    inject_wan_core_loras,
    inject_wan_lora_multi,
    load_workflow,
    required_anchors_for_video_task,
    required_anchors_for_wan22_audio_task,
    validate_anchors,
)
from app.validation import (
    WanVideoAdvancedOptions,
    WanVideoDecodeOptions,
    WanVideoEncodeOptions,
    WanVideoExportOptions,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Anchor validation for workflow files
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# inject_video_weights — core node field names
# ---------------------------------------------------------------------------

def _make_core_model_cfg(**overrides) -> ModelConfig:
    base = dict(
        name="fixture-wan",
        backend="comfyui",
        family="wan22",
        prediction="eps",
        workflow_path="workflows/wan22_i2v_api.json",
        checkpoint="diffusion_models/SmoothMix_I2V_v2_High.safetensors",
        vae="vae/Wan2_1_VAE_fp32.safetensors",
        vram_estimate_gb=11.0,
        wan_t5_encoder="text_encoders/umt5-xxl-enc-bf16.safetensors",
        capabilities={"video_gen": True, "video_task": "i2v"},
    )
    base.update(overrides)
    return ModelConfig(**base)


def test_inject_video_weights_core_unet_uses_unet_name() -> None:
    """UNETLoader expects unet_name, not model."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    inject_video_weights(graph, model_cfg=_make_core_model_cfg())
    model_id = find_anchor(graph, "%WAN_MODEL%")
    assert graph[model_id]["class_type"] == "UNETLoader"
    assert graph[model_id]["inputs"]["unet_name"] == "SmoothMix_I2V_v2_High.safetensors"
    assert "model" not in graph[model_id]["inputs"]


def test_inject_video_weights_core_vae_uses_vae_name() -> None:
    """VAELoader expects vae_name; no precision field injected for core loader."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    inject_video_weights(graph, model_cfg=_make_core_model_cfg())
    vae_id = find_anchor(graph, "%WAN_VAE%")
    assert graph[vae_id]["class_type"] == "VAELoader"
    inp = graph[vae_id]["inputs"]
    assert inp["vae_name"] == "Wan2_1_VAE_fp32.safetensors"
    assert "model_name" not in inp
    assert "precision" not in inp


def test_inject_video_weights_core_t5_uses_clip_name() -> None:
    """CLIPLoader expects clip_name, not model_name."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_t2v_api.json")
    inject_video_weights(graph, model_cfg=_make_core_model_cfg(
        workflow_path="workflows/wan22_t2v_api.json",
        checkpoint="diffusion_models/SmoothMix_T2V_High_v3.safetensors",
        capabilities={"video_gen": True, "video_task": "t2v"},
    ))
    t5_id = find_anchor(graph, "%WAN_T5%")
    assert graph[t5_id]["class_type"] == "CLIPLoader"
    assert graph[t5_id]["inputs"]["clip_name"] == "umt5-xxl-enc-bf16.safetensors"
    assert "model_name" not in graph[t5_id]["inputs"]


# ---------------------------------------------------------------------------
# inject_wan_core_loras
# ---------------------------------------------------------------------------


def test_inject_wan_core_loras_empty_is_noop() -> None:
    """No LoRAs → graph unchanged (no LoraLoaderModelOnly nodes added)."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    before_keys = set(graph)
    inject_wan_core_loras(graph, ())
    assert set(graph) == before_keys


def test_inject_wan_core_loras_inserts_chain() -> None:
    """LoRAs are inserted as LoraLoaderModelOnly chain between UNETLoader and ModelSamplingSD3."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    inject_wan_core_loras(
        graph,
        [
            ResolvedLoraRef(name="lora_a", weight=0.8),
            ResolvedLoraRef(name="sub/lora_b", weight=1.2),
        ],
    )
    lora_nodes = [
        nid for nid, n in graph.items() if n.get("class_type") == "LoraLoaderModelOnly"
    ]
    assert len(lora_nodes) == 2

    # Verify filenames.
    names = {graph[nid]["inputs"]["lora_name"] for nid in lora_nodes}
    assert "lora_a.safetensors" in names
    assert "sub/lora_b.safetensors" in names

    # Verify strengths.
    for nid in lora_nodes:
        inp = graph[nid]["inputs"]
        if inp["lora_name"] == "lora_a.safetensors":
            assert inp["strength_model"] == pytest.approx(0.8)
        else:
            assert inp["strength_model"] == pytest.approx(1.2)


def test_inject_wan_core_loras_shift_rewired() -> None:
    """ModelSamplingSD3 (%WAN_SHIFT%) model input must point to the last LoRA, not UNETLoader."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    inject_wan_core_loras(
        graph,
        [ResolvedLoraRef(name="x", weight=1.0)],
    )
    shift_id = find_anchor(graph, "%WAN_SHIFT%")
    model_wire = graph[shift_id]["inputs"]["model"]
    # Should not point to UNETLoader any more.
    unet_id = find_anchor(graph, "%WAN_MODEL%")
    assert model_wire[0] != unet_id

    # The node it points to must be LoraLoaderModelOnly.
    chain_node_id = str(model_wire[0])
    assert graph[chain_node_id]["class_type"] == "LoraLoaderModelOnly"


def test_inject_wan_core_loras_chain_order() -> None:
    """First LoRA feeds from UNETLoader; second feeds from first."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_t2v_api.json")
    inject_wan_core_loras(
        graph,
        [
            ResolvedLoraRef(name="first", weight=0.5),
            ResolvedLoraRef(name="second", weight=0.7),
        ],
    )
    lora_nodes = sorted(
        [(nid, n) for nid, n in graph.items() if n.get("class_type") == "LoraLoaderModelOnly"],
        key=lambda x: int(x[0]),
    )
    assert len(lora_nodes) == 2

    # First node must pull from UNETLoader.
    unet_id = find_anchor(graph, "%WAN_MODEL%")
    first_nid, first_node = lora_nodes[0]
    assert str(first_node["inputs"]["model"][0]) == str(unet_id)

    # Second node must pull from first node.
    second_nid, second_node = lora_nodes[1]
    assert str(second_node["inputs"]["model"][0]) == first_nid


# ---------------------------------------------------------------------------
# inject_wan_lora_multi — legacy Kijai path (tested via inline graph)
# ---------------------------------------------------------------------------

def _make_kijai_graph() -> dict:
    """Minimal graph with WanVideoLoraSelectMulti and WanVideoModelLoader."""
    return {
        "90": {
            "class_type": "WanVideoLoraSelectMulti",
            "inputs": {
                "lora_0": "none", "strength_0": 1.0,
                "lora_1": "none", "strength_1": 1.0,
                "lora_2": "none", "strength_2": 1.0,
                "lora_3": "none", "strength_3": 1.0,
                "lora_4": "none", "strength_4": 1.0,
                "merge_loras": True,
                "low_mem_load": False,
            },
            "_meta": {"title": "%WAN_LORA_MULTI%"},
        },
        "4": {
            "class_type": "WanVideoModelLoader",
            "inputs": {
                "model": "placeholder.safetensors",
                "lora": ["90", 0],
            },
            "_meta": {"title": "%WAN_MODEL%"},
        },
    }


def test_inject_wan_lora_multi_empty_disconnects_loader() -> None:
    graph = _make_kijai_graph()
    inject_wan_lora_multi(graph, ())
    assert "lora" not in graph["4"]["inputs"]


def test_inject_wan_lora_multi_nonempty_keeps_loader_wire() -> None:
    graph = _make_kijai_graph()
    inject_wan_lora_multi(graph, (ResolvedLoraRef(name="a", weight=0.5),))
    assert graph["4"]["inputs"]["lora"] == ["90", 0]


def test_inject_wan_lora_multi_maps_order_and_flags() -> None:
    graph = _make_kijai_graph()
    inject_wan_lora_multi(
        graph,
        (
            ResolvedLoraRef(name="sub/a", weight=0.5),
            ResolvedLoraRef(name="b", weight=1.25),
        ),
        merge_loras=False,
        low_mem_load=True,
    )
    ins = graph["90"]["inputs"]
    assert ins["lora_0"] == "sub/a.safetensors"
    assert ins["strength_0"] == 0.5
    assert ins["lora_1"] == "b.safetensors"
    assert ins["strength_1"] == 1.25
    assert ins["lora_2"] == "none"
    assert ins["merge_loras"] is False
    assert ins["low_mem_load"] is True


# ---------------------------------------------------------------------------
# apply_wan_advanced_patches — core workflow behaviour
# ---------------------------------------------------------------------------


def test_apply_wan_advanced_export_applies_to_core_vhs() -> None:
    """Export options work on the core VHS_VideoCombine node."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    opts = WanVideoAdvancedOptions(export=WanVideoExportOptions(crf=22, pix_fmt="yuv420p"))
    apply_wan_advanced_patches(graph, opts, video_task="i2v")
    out_id = find_anchor(graph, "%VIDEO_OUTPUT%")
    assert graph[out_id]["inputs"]["crf"] == 22
    assert graph[out_id]["inputs"]["pix_fmt"] == "yuv420p"


def test_apply_wan_advanced_encode_silently_skipped_for_core_i2v() -> None:
    """wan_advanced.encode options are silently ignored for WanImageToVideo (core workflow)."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_i2v_api.json")
    opts = WanVideoAdvancedOptions(
        encode=WanVideoEncodeOptions(noise_aug_strength=0.07),
        export=WanVideoExportOptions(crf=19),
    )
    apply_wan_advanced_patches(graph, opts, video_task="i2v")
    dims_id = find_anchor(graph, "%WAN_DIMS%")
    # encode options must NOT be injected into WanImageToVideo
    assert "noise_aug_strength" not in graph[dims_id]["inputs"]
    # but export must still be applied
    out_id = find_anchor(graph, "%VIDEO_OUTPUT%")
    assert graph[out_id]["inputs"]["crf"] == 19


def test_apply_wan_advanced_t2v_skips_encode() -> None:
    """encode is always ignored for t2v (no start image, no I2V conditioning)."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_t2v_api.json")
    opts = WanVideoAdvancedOptions(
        encode=WanVideoEncodeOptions(noise_aug_strength=0.99),
        export=WanVideoExportOptions(crf=21),
    )
    apply_wan_advanced_patches(graph, opts, video_task="t2v")
    dims_id = find_anchor(graph, "%WAN_DIMS%")
    assert "noise_aug_strength" not in graph[dims_id]["inputs"]
    out_id = find_anchor(graph, "%VIDEO_OUTPUT%")
    assert graph[out_id]["inputs"]["crf"] == 21


def test_apply_wan_advanced_decode_silently_skipped_for_core() -> None:
    """wan_advanced.decode options are silently ignored for core VAEDecode."""
    graph = load_workflow(REPO_ROOT / "workflows" / "wan22_t2v_api.json")
    opts = WanVideoAdvancedOptions(decode=WanVideoDecodeOptions(enable_vae_tiling=True))
    # Must not raise even though VAEDecode has no tiling knobs.
    apply_wan_advanced_patches(graph, opts, video_task="t2v")
    dec_id = find_anchor(graph, "%WAN_DECODE%")
    assert "enable_vae_tiling" not in graph[dec_id]["inputs"]
