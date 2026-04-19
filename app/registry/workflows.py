from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Required anchors for an SDXL workflow. `%LORA_INSERT%` is retained here as a
# *marker* for where the LoRA chain logically threads in — Cycle 5's actual
# injection reads from `%MODEL_SOURCE%`/`%CLIP_SOURCE%` and rewrites downstream
# consumers, without consuming `%LORA_INSERT%` directly. The anchor stays
# mandatory so workflow authors document the injection site; a future cycle can
# tighten injection to actually locate the anchor if we ever support templates
# where LoRA insertion isn't colocated with the checkpoint loader.
REQUIRED_ANCHORS_SDXL: tuple[str, ...] = (
    "%MODEL_SOURCE%",
    "%CLIP_SOURCE%",
    "%LORA_INSERT%",
    "%POSITIVE_PROMPT%",
    "%NEGATIVE_PROMPT%",
    "%KSAMPLER%",
    "%OUTPUT%",
)


class WorkflowValidationError(Exception):
    """Workflow JSON was not parseable or failed anchor validation."""


@dataclass(frozen=True, slots=True)
class ResolvedLoraRef:
    """Runtime LoRA reference for graph injection.

    Distinct from `app.validation.LoraSpec` (the Pydantic request model). This
    type represents a post-validation reference the graph injector consumes.
    Populated by `resolve_and_validate` after realpath-containment + existence
    checks succeed.
    """

    name: str
    weight: float


def load_workflow(path: str | Path) -> dict[str, dict]:
    """Parse the JSON file and return the ComfyUI prompt-API graph dict."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkflowValidationError(f"workflow file not found: {p}") from exc
    except OSError as exc:
        raise WorkflowValidationError(f"workflow file unreadable: {p} ({exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowValidationError(f"workflow {p} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowValidationError(f"workflow {p} must be an object, got {type(data).__name__}")
    return data


def _title_anchors(node: dict) -> list[str]:
    """Return the anchor list declared in a node's _meta.title.

    Convention: title is a single string; multi-anchor is encoded as comma-separated
    (e.g. "%MODEL_SOURCE%,%CLIP_SOURCE%"). Whitespace around commas is tolerated.
    Non-anchor titles (like "vae:decode") contribute nothing.
    """
    meta = node.get("_meta") or {}
    title = meta.get("title")
    if not isinstance(title, str):
        return []
    parts = [p.strip() for p in title.split(",")]
    return [p for p in parts if p.startswith("%") and p.endswith("%")]


def validate_anchors(graph: dict[str, dict], required: Sequence[str]) -> None:
    """Confirm each required anchor appears on exactly one node.

    Raises WorkflowValidationError listing the problem set (missing, duplicated).
    """
    owners: dict[str, list[str]] = {anchor: [] for anchor in required}
    for node_id, node in graph.items():
        for anchor in _title_anchors(node):
            if anchor in owners:
                owners[anchor].append(node_id)

    missing = [a for a, ids in owners.items() if not ids]
    duplicated = {a: ids for a, ids in owners.items() if len(ids) > 1}
    problems: list[str] = []
    if missing:
        problems.append(f"missing anchors: {missing}")
    if duplicated:
        problems.append(f"duplicate anchors (appear on >1 node): {duplicated}")
    if problems:
        raise WorkflowValidationError("; ".join(problems))


def find_anchor(graph: dict[str, dict], anchor: str) -> str:
    """Return the node id whose _meta.title declares `anchor`.

    Raises KeyError if no node declares it. Match is exact — `%MODEL%` does NOT
    match `%MODEL_SOURCE%`.
    """
    for node_id, node in graph.items():
        if anchor in _title_anchors(node):
            return node_id
    raise KeyError(f"anchor {anchor!r} not found in graph")


def _rewrite_inputs(
    graph: dict[str, dict],
    *,
    source_id: str,
    source_slot: int,
    new_id: str,
    new_slot: int,
    skip_ids: set[str],
) -> None:
    """Rewrite every `[source_id, source_slot]` reference to `[new_id, new_slot]`.

    Skips nodes whose id is in `skip_ids` (the new LoraLoader chain itself, which
    legitimately keeps references to the anchor node as the head of the chain).
    """
    for node_id, node in graph.items():
        if node_id in skip_ids:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, value in list(inputs.items()):
            if (
                isinstance(value, list)
                and len(value) == 2
                and value[0] == source_id
                and value[1] == source_slot
            ):
                inputs[key] = [new_id, new_slot]


def inject_loras(
    graph: dict[str, dict],
    loras: Sequence[ResolvedLoraRef],
    *,
    model_cfg,
) -> None:
    """Implements arch §9 algorithm. Mutates `graph` in place.

    1. Find the nodes owning %MODEL_SOURCE% and %CLIP_SOURCE% anchors.
    2. Chain a LoraLoader node per entry; model+clip feed from anchor on the first,
       from previous node on subsequent. Output slots: model=0, clip=1.
    3. Rewrite downstream consumers of the anchor's model(slot 0) / clip(slot 1)
       outputs to point at the final chain node. The chain nodes themselves are
       skipped during rewrite (they need to reach back to the anchor).

    Empty `loras` list → no-op. Raises WorkflowValidationError if required anchors
    are missing (shouldn't happen — registry validates graphs at load).
    """
    if not loras:
        return

    try:
        model_source_id = find_anchor(graph, "%MODEL_SOURCE%")
        clip_source_id = find_anchor(graph, "%CLIP_SOURCE%")
    except KeyError as exc:
        raise WorkflowValidationError(f"inject_loras: {exc}") from exc

    int_keys = [int(k) for k in graph.keys() if k.isdigit()]
    next_id = max(int_keys) + 1 if int_keys else 1

    chain_ids: list[str] = []
    prev_model_ref: list = [model_source_id, 0]
    prev_clip_ref: list = [clip_source_id, 1]
    for lora in loras:
        node_id = str(next_id)
        next_id += 1
        graph[node_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": f"{lora.name}.safetensors",
                "strength_model": float(lora.weight),
                "strength_clip": float(lora.weight),
                "model": prev_model_ref,
                "clip": prev_clip_ref,
            },
            "_meta": {"title": f"lora:{lora.name}"},
        }
        chain_ids.append(node_id)
        prev_model_ref = [node_id, 0]
        prev_clip_ref = [node_id, 1]

    last_id = chain_ids[-1]
    skip = set(chain_ids)
    _rewrite_inputs(
        graph,
        source_id=model_source_id,
        source_slot=0,
        new_id=last_id,
        new_slot=0,
        skip_ids=skip,
    )
    _rewrite_inputs(
        graph,
        source_id=clip_source_id,
        source_slot=1,
        new_id=last_id,
        new_slot=1,
        skip_ids=skip,
    )


def inject_vpred(graph: dict[str, dict], *, model_cfg) -> None:
    """v-prediction workflow injection — arch v0.5 deferred.

    Primary guard lives in `load_registry` (rejects any `prediction="vpred"`
    entry at boot). This per-request NotImplementedError is defense-in-depth.
    """
    prediction = getattr(model_cfg, "prediction", None)
    if prediction == "vpred":
        raise NotImplementedError(
            "vpred injection deferred per arch v0.5; "
            "re-enable when a vpred model is added to config/models.yaml"
        )
