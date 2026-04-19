from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from app.backends.base import ModelConfig
from app.registry.workflows import (
    REQUIRED_ANCHORS_SDXL,
    WorkflowValidationError,
    load_workflow,
    validate_anchors,
)

_ALLOWED_BACKENDS: frozenset[str] = frozenset({"comfyui"})
_ALLOWED_PREDICTIONS: frozenset[str] = frozenset({"eps", "vpred"})

log = structlog.get_logger(__name__)


class RegistryValidationError(Exception):
    """Startup validation failed. `stage` identifies the specific check."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


class Registry:
    """Immutable lookup table of model name → ModelConfig."""

    def __init__(self, models: dict[str, ModelConfig]) -> None:
        self._models = dict(models)

    def get(self, name: str) -> ModelConfig:
        if name not in self._models:
            raise KeyError(name)
        return self._models[name]

    def names(self) -> list[str]:
        return list(self._models.keys())

    def all(self) -> list[ModelConfig]:
        return list(self._models.values())


def _parse_entry(raw: dict) -> ModelConfig:
    return ModelConfig(
        name=raw["name"],
        backend=raw.get("backend", "comfyui"),
        workflow_path=raw["workflow"],
        checkpoint=raw["checkpoint"],
        vae=raw.get("vae"),
        vram_estimate_gb=float(raw["vram_estimate_gb"]),
        prediction=raw.get("prediction", "eps"),
        capabilities=raw.get("capabilities") or {},
        defaults=raw.get("defaults") or {},
        limits=raw.get("limits") or {},
    )


def load_registry(
    yaml_path: str | Path,
    *,
    models_root: str | Path,
    workflows_root: str | Path,
    vram_budget_gb: float,
) -> Registry:
    """Parse YAML, validate on-disk artifacts, return a Registry. Fail-fast on any issue."""
    yaml_path = Path(yaml_path)
    models_root = Path(models_root)
    workflows_root = Path(workflows_root)

    try:
        doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise RegistryValidationError("yaml_missing", str(yaml_path)) from exc
    except yaml.YAMLError as exc:
        raise RegistryValidationError("yaml_invalid", str(exc)) from exc

    entries = doc.get("models") or []
    if not entries:
        raise RegistryValidationError("empty_registry", "models.yaml has no entries")

    # Late import to avoid circular: app.validation → app.registry.models → app.validation.
    from app.validation import ALLOWED_SAMPLERS, ALLOWED_SCHEDULERS

    models: dict[str, ModelConfig] = {}
    for raw in entries:
        cfg = _parse_entry(raw)

        if cfg.name in models:
            raise RegistryValidationError(
                "duplicate_name", f"{cfg.name} appears multiple times in YAML"
            )
        if cfg.backend not in _ALLOWED_BACKENDS:
            raise RegistryValidationError(
                "unknown_backend",
                f"{cfg.name}: backend {cfg.backend!r} not in {_ALLOWED_BACKENDS}",
            )
        if cfg.prediction not in _ALLOWED_PREDICTIONS:
            raise RegistryValidationError(
                "unknown_prediction",
                f"{cfg.name}: prediction {cfg.prediction!r} not in {_ALLOWED_PREDICTIONS}",
            )
        default_sampler = (cfg.defaults or {}).get("sampler")
        if default_sampler is not None and default_sampler not in ALLOWED_SAMPLERS:
            raise RegistryValidationError(
                "unknown_sampler",
                f"{cfg.name}: defaults.sampler {default_sampler!r} not in allowed set",
            )
        default_scheduler = (cfg.defaults or {}).get("scheduler")
        if default_scheduler is not None and default_scheduler not in ALLOWED_SCHEDULERS:
            raise RegistryValidationError(
                "unknown_scheduler",
                f"{cfg.name}: defaults.scheduler {default_scheduler!r} not in allowed set",
            )

        # Checkpoint must exist under models_root.
        ckpt_path = models_root / cfg.checkpoint
        if not ckpt_path.exists():
            raise RegistryValidationError(
                "checkpoint_missing", f"{cfg.name}: {ckpt_path} not found"
            )

        # VAE (optional — None means baked-in).
        if cfg.vae is not None:
            vae_path = models_root / cfg.vae
            if not vae_path.exists():
                raise RegistryValidationError("vae_missing", f"{cfg.name}: {vae_path} not found")

        # Workflow file must exist and have required SDXL anchors.
        wf_path = workflows_root / cfg.workflow_path
        if not wf_path.exists():
            raise RegistryValidationError("workflow_missing", f"{cfg.name}: {wf_path} not found")
        try:
            graph = load_workflow(wf_path)
            validate_anchors(graph, REQUIRED_ANCHORS_SDXL)
        except WorkflowValidationError as exc:
            raise RegistryValidationError("anchors_missing", f"{cfg.name}: {exc}") from exc

        # VRAM budget.
        if cfg.vram_estimate_gb > vram_budget_gb:
            raise RegistryValidationError(
                "vram_over_budget",
                f"{cfg.name}: {cfg.vram_estimate_gb} GB > budget {vram_budget_gb} GB",
            )

        models[cfg.name] = cfg

    log.info("registry.loaded", models=list(models.keys()))
    return Registry(models)
