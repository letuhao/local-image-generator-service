from __future__ import annotations

import copy
from typing import Any

import pytest

from app.registry.workflows import (
    ResolvedLoraRef,
    WorkflowValidationError,
    inject_loras,
    inject_vpred,
)


def _fake_model_cfg(prediction: str = "eps") -> Any:
    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.prediction = prediction
    return cfg


def _sdxl_graph() -> dict:
    """Minimal SDXL-shaped graph with MODEL/CLIP sources on node '1'."""
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "base.safetensors"},
            "_meta": {"title": "%MODEL_SOURCE%,%CLIP_SOURCE%,%LORA_INSERT%"},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "pos", "clip": ["1", 1]},
            "_meta": {"title": "%POSITIVE_PROMPT%"},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "neg", "clip": ["1", 1]},
            "_meta": {"title": "%NEGATIVE_PROMPT%"},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["5", 0],
                "seed": 0,
                "steps": 28,
                "cfg": 5.0,
                "sampler_name": "euler_ancestral",
                "scheduler": "karras",
                "denoise": 1.0,
            },
            "_meta": {"title": "%KSAMPLER%"},
        },
    }


def test_inject_loras_empty_list_no_op() -> None:
    g = _sdxl_graph()
    before = copy.deepcopy(g)
    inject_loras(g, [], model_cfg=_fake_model_cfg())
    assert g == before


def test_inject_single_lora_chains_and_rewrites() -> None:
    g = _sdxl_graph()
    inject_loras(
        g,
        [ResolvedLoraRef(name="style_a", weight=0.7)],
        model_cfg=_fake_model_cfg(),
    )
    # New node "7" added (max int key was 6)
    assert "7" in g
    new_node = g["7"]
    assert new_node["class_type"] == "LoraLoader"
    assert new_node["inputs"]["lora_name"] == "style_a.safetensors"
    assert new_node["inputs"]["strength_model"] == pytest.approx(0.7)
    assert new_node["inputs"]["strength_clip"] == pytest.approx(0.7)
    assert new_node["inputs"]["model"] == ["1", 0]
    assert new_node["inputs"]["clip"] == ["1", 1]
    # Downstream consumers rewritten
    assert g["6"]["inputs"]["model"] == ["7", 0]
    assert g["3"]["inputs"]["clip"] == ["7", 1]
    assert g["4"]["inputs"]["clip"] == ["7", 1]


def test_inject_three_loras_chained() -> None:
    g = _sdxl_graph()
    inject_loras(
        g,
        [
            ResolvedLoraRef("a", 0.5),
            ResolvedLoraRef("b", 0.8),
            ResolvedLoraRef("c", 1.0),
        ],
        model_cfg=_fake_model_cfg(),
    )
    # IDs 7, 8, 9 created
    assert g["7"]["inputs"]["lora_name"] == "a.safetensors"
    assert g["7"]["inputs"]["model"] == ["1", 0]
    assert g["7"]["inputs"]["clip"] == ["1", 1]
    assert g["8"]["inputs"]["lora_name"] == "b.safetensors"
    assert g["8"]["inputs"]["model"] == ["7", 0]
    assert g["8"]["inputs"]["clip"] == ["7", 1]
    assert g["9"]["inputs"]["lora_name"] == "c.safetensors"
    assert g["9"]["inputs"]["model"] == ["8", 0]
    assert g["9"]["inputs"]["clip"] == ["8", 1]
    # Final consumers point at last chain node
    assert g["6"]["inputs"]["model"] == ["9", 0]
    assert g["3"]["inputs"]["clip"] == ["9", 1]


def test_inject_subdir_name() -> None:
    g = _sdxl_graph()
    inject_loras(
        g,
        [ResolvedLoraRef(name="hanfu/Bai_LingMiao", weight=0.8)],
        model_cfg=_fake_model_cfg(),
    )
    assert g["7"]["inputs"]["lora_name"] == "hanfu/Bai_LingMiao.safetensors"


def test_inject_does_not_mutate_template_if_copy_supplied() -> None:
    template = _sdxl_graph()
    working = copy.deepcopy(template)
    inject_loras(
        working,
        [ResolvedLoraRef("a", 0.5)],
        model_cfg=_fake_model_cfg(),
    )
    # Template untouched
    assert "7" not in template
    assert template["6"]["inputs"]["model"] == ["1", 0]
    # Working graph changed
    assert "7" in working
    assert working["6"]["inputs"]["model"] == ["7", 0]


def test_inject_loras_missing_anchor_raises() -> None:
    g = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {},
            "_meta": {"title": "something_else"},
        },
    }
    with pytest.raises(WorkflowValidationError):
        inject_loras(
            g,
            [ResolvedLoraRef("a", 0.5)],
            model_cfg=_fake_model_cfg(),
        )


def test_inject_vpred_eps_is_no_op() -> None:
    g = _sdxl_graph()
    before = copy.deepcopy(g)
    inject_vpred(g, model_cfg=_fake_model_cfg(prediction="eps"))
    assert g == before


def test_inject_vpred_vpred_raises() -> None:
    g = _sdxl_graph()
    with pytest.raises(NotImplementedError):
        inject_vpred(g, model_cfg=_fake_model_cfg(prediction="vpred"))
