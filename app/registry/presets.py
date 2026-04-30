from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.registry.models import Registry

_ALLOWED_PRESET_STATUSES = frozenset({"draft", "approved", "deprecated"})
_LORA_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_/\-.]*$")


class PresetRegistryValidationError(Exception):
    """Preset registry validation failed."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PresetLora:
    name: str
    weight: float


@dataclass(frozen=True, slots=True)
class PresetConfig:
    id: str
    version: str
    asset_type: str
    status: str
    confirmed: bool
    description: str
    model: str
    loras: tuple[PresetLora, ...] = field(default_factory=tuple)
    generation_defaults: dict[str, Any] = field(default_factory=dict)
    supported_asset_types: tuple[str, ...] = field(default_factory=tuple)
    variables_schema: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class PresetRegistry:
    def __init__(self, presets: dict[str, PresetConfig]) -> None:
        self._presets = dict(presets)

    def get(self, preset_id: str) -> PresetConfig:
        try:
            return self._presets[preset_id]
        except KeyError:
            raise KeyError(preset_id) from None

    def all(self) -> list[PresetConfig]:
        return list(self._presets.values())

    def confirmed(self) -> list[PresetConfig]:
        return [p for p in self._presets.values() if p.confirmed]

    def confirmed_for_model(self, model_name: str) -> list[PresetConfig]:
        return [p for p in self.confirmed() if p.model == model_name]


def _parse_preset(raw: dict[str, Any]) -> PresetConfig:
    bundle = raw.get("bundle") or {}
    loras_raw = bundle.get("loras") or []
    loras: list[PresetLora] = []
    for l in loras_raw:
        loras.append(PresetLora(name=str(l["name"]), weight=float(l["weight"])))
    return PresetConfig(
        id=str(raw["id"]),
        version=str(raw["version"]),
        asset_type=str(raw["asset_type"]),
        status=str(raw.get("status", "draft")),
        confirmed=bool(raw.get("confirmed", False)),
        description=str(raw.get("description", "")),
        model=str(bundle["model"]),
        loras=tuple(loras),
        generation_defaults=raw.get("generation_defaults") or {},
        supported_asset_types=tuple(raw.get("supported_asset_types") or []),
        variables_schema=tuple(raw.get("variables_schema") or []),
    )


def load_preset_registry(yaml_path: str | Path, *, models: Registry) -> PresetRegistry:
    yaml_path = Path(yaml_path)
    try:
        doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise PresetRegistryValidationError("preset_yaml_missing", str(yaml_path)) from exc
    except yaml.YAMLError as exc:
        raise PresetRegistryValidationError("preset_yaml_invalid", str(exc)) from exc

    entries = doc.get("presets") or []
    if not entries:
        raise PresetRegistryValidationError("preset_empty_registry", "no presets defined")

    parsed: dict[str, PresetConfig] = {}
    known_model_names = set(models.names())
    for raw in entries:
        preset = _parse_preset(raw)
        if preset.id in parsed:
            raise PresetRegistryValidationError(
                "preset_duplicate_id",
                f"{preset.id} appears multiple times",
            )
        if preset.status not in _ALLOWED_PRESET_STATUSES:
            raise PresetRegistryValidationError(
                "preset_unknown_status",
                f"{preset.id}: status {preset.status!r} not in {_ALLOWED_PRESET_STATUSES}",
            )
        if preset.model not in known_model_names:
            raise PresetRegistryValidationError(
                "preset_unknown_model",
                f"{preset.id}: model {preset.model!r} not found in model registry",
            )
        for l in preset.loras:
            if not _LORA_NAME_RE.fullmatch(l.name):
                raise PresetRegistryValidationError(
                    "preset_lora_name_invalid",
                    f"{preset.id}: lora name {l.name!r} is invalid",
                )
        if preset.confirmed:
            if not preset.description.strip():
                raise PresetRegistryValidationError(
                    "preset_confirmed_missing_description",
                    f"{preset.id}: confirmed preset requires non-empty description",
                )
            required_defaults = ("size", "steps", "cfg")
            missing = [k for k in required_defaults if k not in preset.generation_defaults]
            if missing:
                raise PresetRegistryValidationError(
                    "preset_confirmed_missing_defaults",
                    f"{preset.id}: confirmed preset missing defaults: {', '.join(missing)}",
                )
        parsed[preset.id] = preset

    return PresetRegistry(parsed)
