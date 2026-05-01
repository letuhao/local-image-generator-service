from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.base import ModelConfig
from app.registry.models import Registry
from app.registry.presets import PresetRegistryValidationError, load_preset_registry


@pytest.fixture
def model_registry() -> Registry:
    terrain = ModelConfig(
        name="terrain-sdxl-base",
        backend="comfyui",
        workflow_path="workflows/sdxl_eps.json",
        checkpoint="checkpoints/sd_xl_base_1.0.safetensors",
        vae="vae/sdxl_vae.safetensors",
        vram_estimate_gb=7.0,
        prediction="eps",
        capabilities={"image_gen": True},
        defaults={},
        limits={},
    )
    flux_q8 = ModelConfig(
        name="flux1-dev-q8",
        backend="comfyui",
        family="flux",
        workflow_path="workflows/flux_gguf.json",
        checkpoint="unet/flux1-dev-Q8_0.gguf",
        vae="vae/ae.safetensors",
        clip_l="text_encoders/clip_l.safetensors",
        t5xxl="text_encoders/t5xxl_fp8_e4m3fn.safetensors",
        dual_clip_type="flux",
        vram_estimate_gb=10.0,
        prediction="eps",
        capabilities={"image_gen": True},
        defaults={
            "size": "1024x1024",
            "steps": 24,
            "cfg": 1.0,
            "sampler": "euler",
            "scheduler": "simple",
            "negative_prompt": "",
        },
        limits={"steps_max": 50, "n_max": 2, "size_max_pixels": 1572864},
    )
    flux_tree = ModelConfig(
        name="flux1-dev-q8-tree",
        backend="comfyui",
        family="flux",
        workflow_path="workflows/flux_gguf.json",
        checkpoint="unet/flux1-dev-Q8_0.gguf",
        vae="vae/ae.safetensors",
        clip_l="text_encoders/clip_l.safetensors",
        t5xxl="text_encoders/t5xxl_fp8_e4m3fn.safetensors",
        dual_clip_type="flux",
        vram_estimate_gb=10.0,
        prediction="eps",
        capabilities={"image_gen": True},
        defaults={
            "size": "1024x1024",
            "steps": 24,
            "cfg": 1.0,
            "sampler": "euler",
            "scheduler": "simple",
            "negative_prompt": "terrain tile",
        },
        limits={"steps_max": 60, "n_max": 2, "size_max_pixels": 1572864},
    )
    return Registry(
        {terrain.name: terrain, flux_q8.name: flux_q8, flux_tree.name: flux_tree}
    )


def test_load_preset_registry_happy_path(model_registry: Registry) -> None:
    reg = load_preset_registry("config/presets/catalog.yaml", models=model_registry)
    p = reg.get("terrain-53858-v1")
    assert p.confirmed is True
    assert p.model == "terrain-sdxl-base"
    assert len(p.loras) == 1
    terrain_tile = reg.get("flux-terrain-tile-draft-v1")
    assert terrain_tile.asset_type == "terrain_tile"
    assert terrain_tile.model == "flux1-dev-q8"
    assert reg.get("flux-structure-sprite-draft-v1").asset_type == "structure_sprite"
    assert reg.get("flux-flora-sprite-draft-v1").asset_type == "flora_sprite"


def test_load_preset_registry_rejects_unknown_model(
    tmp_path: Path, model_registry: Registry
) -> None:
    f = tmp_path / "bad.yaml"
    f.write_text(
        """
presets:
  - id: bad-one
    version: 1.0.0
    asset_type: tile_texture
    status: approved
    confirmed: true
    description: bad
    bundle:
      model: unknown-model
      loras: []
    generation_defaults:
      size: "1024x1024"
      steps: 28
      cfg: 5.5
""",
        encoding="utf-8",
    )
    with pytest.raises(PresetRegistryValidationError, match="preset_unknown_model"):
        load_preset_registry(f, models=model_registry)
