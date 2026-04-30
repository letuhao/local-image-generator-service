from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from app.startup.checks import (
    StartupCheckError,
    StartupContext,
    run_startup_checks,
)


def _write_registry_fixture(
    tmp_path: Path, *, with_required_anchors: bool = True
) -> StartupContext:
    models_root = tmp_path / "models"
    workflows_root = tmp_path / "workflows"
    yaml_path = tmp_path / "models.yaml"

    ckpt = models_root / "checkpoints" / "test.safetensors"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_bytes(b"ckpt")

    vae = models_root / "vae" / "test.vae.safetensors"
    vae.parent.mkdir(parents=True, exist_ok=True)
    vae.write_bytes(b"vae")

    workflow = workflows_root / "test.json"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    if with_required_anchors:
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
    else:
        graph = {
            "1": {
                "class_type": "X",
                "inputs": {},
                "_meta": {"title": "%MODEL_SOURCE%"},
            }
        }
    workflow.write_text(json.dumps(graph), encoding="utf-8")

    yaml_path.write_text(
        yaml.safe_dump(
            {
                "models": [
                    {
                        "name": "test-model",
                        "backend": "comfyui",
                        "workflow": "workflows/test.json",
                        "checkpoint": "checkpoints/test.safetensors",
                        "vae": "vae/test.vae.safetensors",
                        "prediction": "eps",
                        "capabilities": {"image_gen": True},
                        "defaults": {"sampler": "euler_ancestral", "scheduler": "karras"},
                        "limits": {"steps_max": 10, "n_max": 1, "size_max_pixels": 65536},
                        "vram_estimate_gb": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    return StartupContext(
        imagegen_env="prod",
        models_yaml_path=yaml_path,
        models_root=models_root,
        workflows_root=tmp_path,
        vram_budget_gb=4,
        comfyui_url="http://127.0.0.1:8188",
        webhook_allowed_hosts="example.com",
        webhook_allow_any_host=False,
        webhook_signing_secrets="secret",
    )


def test_prod_requires_non_empty_webhook_allowlist(tmp_path: Path) -> None:
    ctx = _write_registry_fixture(tmp_path)
    ctx = replace(ctx, webhook_allowed_hosts="")
    with pytest.raises(StartupCheckError) as exc:
        run_startup_checks(ctx)
    assert exc.value.stage == "webhook_allowed_hosts_empty"


def test_prod_forbids_allow_any_host(tmp_path: Path) -> None:
    ctx = _write_registry_fixture(tmp_path)
    ctx = replace(ctx, webhook_allow_any_host=True)
    with pytest.raises(StartupCheckError) as exc:
        run_startup_checks(ctx)
    assert exc.value.stage == "webhook_allow_any_host_forbidden"


def test_prod_requires_signing_secret(tmp_path: Path) -> None:
    ctx = _write_registry_fixture(tmp_path)
    ctx = replace(ctx, webhook_signing_secrets="")
    with pytest.raises(StartupCheckError) as exc:
        run_startup_checks(ctx)
    assert exc.value.stage == "webhook_signing_secrets_empty"


def test_public_comfy_host_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _write_registry_fixture(tmp_path)
    ctx = replace(ctx, comfyui_url="http://example.com:8188")

    def _fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple]:
        return [(2, 1, 6, "", ("8.8.8.8", 8188))]

    monkeypatch.setattr("app.startup.checks.socket.getaddrinfo", _fake_getaddrinfo)
    with pytest.raises(StartupCheckError) as exc:
        run_startup_checks(ctx)
    assert exc.value.stage == "comfyui_public_ip_forbidden"


def test_missing_checkpoint_fails_startup(tmp_path: Path) -> None:
    ctx = _write_registry_fixture(tmp_path)
    (ctx.models_root / "checkpoints" / "test.safetensors").unlink()
    with pytest.raises(StartupCheckError) as exc:
        run_startup_checks(ctx)
    assert exc.value.stage == "registry_validation_failed"
    assert "checkpoint_missing" in exc.value.reason


def test_missing_workflow_anchor_fails_startup(tmp_path: Path) -> None:
    ctx = _write_registry_fixture(tmp_path, with_required_anchors=False)
    with pytest.raises(StartupCheckError) as exc:
        run_startup_checks(ctx)
    assert exc.value.stage == "registry_validation_failed"
    assert "anchors_missing" in exc.value.reason
