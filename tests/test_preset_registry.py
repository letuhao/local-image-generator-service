from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.base import ModelConfig
from app.registry.models import Registry
from app.registry.presets import PresetRegistryValidationError, load_preset_registry


@pytest.fixture
def model_registry() -> Registry:
    cfg = ModelConfig(
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
    return Registry({cfg.name: cfg})


def test_load_preset_registry_happy_path(model_registry: Registry) -> None:
    reg = load_preset_registry("config/presets/catalog.yaml", models=model_registry)
    p = reg.get("terrain-53858-v1")
    assert p.confirmed is True
    assert p.model == "terrain-sdxl-base"
    assert len(p.loras) == 1


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
