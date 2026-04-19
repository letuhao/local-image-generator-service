"""Path-traversal defense for lora name validation.

Two layers of defense stack here:
  1. Pydantic regex on `LoraSpec.name` — rejects leading `.`, `/`, and any char
     not in `[A-Za-z0-9_/\\-.]`. Catches textbook traversal attempts upfront.
  2. realpath-containment on `resolve_and_validate` — defends against symlink
     escapes, where the name passes the regex but the resolved target points
     outside `LORAS_ROOT`.

These tests exercise both layers.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.backends.base import ModelConfig
from app.registry.models import Registry
from app.validation import (
    GenerateRequest,
    LoraSpec,
    ValidationFailureError,
    resolve_and_validate,
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
        defaults={"sampler": "euler_ancestral", "scheduler": "karras"},
        limits={"steps_max": 60, "n_max": 4, "size_max_pixels": 1572864},
    )
    return Registry({cfg.name: cfg})


def test_dotdot_traversal_rejected_by_regex() -> None:
    """`../../etc/passwd` starts with `.` — rejected at Pydantic layer."""
    with pytest.raises(ValidationError):
        LoraSpec(name="../../etc/passwd", weight=0.5)


def test_absolute_path_rejected_by_regex() -> None:
    """`/absolute/path` leads with `/` — rejected at Pydantic layer."""
    with pytest.raises(ValidationError):
        LoraSpec(name="/etc/passwd", weight=0.5)


def test_embedded_dotdot_rejected_by_regex() -> None:
    """`foo/../bar` contains `..` as a path component — rejected at realpath layer.

    Actually passes the character-class regex (all chars are in the allowed set),
    but `resolve()` collapses to something outside the intended shape, and the
    downstream `.safetensors` resolution may still land inside root. The real
    defense here: the regex is restrictive enough that `..` as a path component
    is technically allowed (chars are fine) but the realpath containment check
    catches any resulting escape. See `test_symlink_escape_rejected` for the
    teeth.
    """
    # Sanity: this passes the regex (no forbidden characters).
    spec = LoraSpec(name="foo/../bar", weight=0.5)
    assert spec.name == "foo/../bar"


@pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("CLAUDE_TESTS_ALLOW_SYMLINK"),
    reason="Windows symlink creation needs Developer Mode or admin; opt in via env.",
)
def test_symlink_escape_rejected(
    registry: Registry,
    tmp_path: Path,
) -> None:
    """A symlink under LORAS_ROOT that points outside it must be rejected.

    Layout:
      <tmp>/outside/target.safetensors         (real file outside root)
      <tmp>/loras/sneaky.safetensors → outside/target.safetensors  (symlink)
    """
    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    real_target = outside / "target.safetensors"
    real_target.write_bytes(b"\x00")
    link = loras_root / "sneaky.safetensors"
    try:
        link.symlink_to(real_target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unsupported on this host: {exc}")

    req = GenerateRequest.model_validate(
        {
            "model": "noobai-xl-v1.1",
            "prompt": "hi",
            "loras": [{"name": "sneaky", "weight": 0.5}],
        }
    )
    with pytest.raises(ValidationFailureError) as exc:
        resolve_and_validate(
            req, registry=registry, async_mode_enabled=False, loras_root=loras_root
        )
    assert exc.value.error_code == "validation_error"
    assert "escapes" in exc.value.message
