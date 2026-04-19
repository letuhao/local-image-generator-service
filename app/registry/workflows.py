from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

# Required anchors for an SDXL workflow. Cycle 2 uses the full set even though
# %LORA_INSERT% / %CLIP_SOURCE% aren't consumed until Cycle 5 — validating them
# now means the Cycle 5 injection code doesn't need to relax today's templates.
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
