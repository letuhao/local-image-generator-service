from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.registry.workflows import (
    REQUIRED_ANCHORS_SDXL,
    WorkflowValidationError,
    find_anchor,
    load_workflow,
    validate_anchors,
)


@pytest.fixture
def valid_graph() -> dict[str, dict]:
    """Minimum graph covering every required SDXL anchor exactly once."""
    return {
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


def test_load_workflow_returns_parsed_dict(tmp_path: Path) -> None:
    path = tmp_path / "w.json"
    path.write_text(json.dumps({"1": {"class_type": "X", "inputs": {}}}))
    assert load_workflow(path) == {"1": {"class_type": "X", "inputs": {}}}


def test_load_workflow_raises_on_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("this is not json {")
    with pytest.raises(WorkflowValidationError):
        load_workflow(path)


def test_load_workflow_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WorkflowValidationError):
        load_workflow(tmp_path / "does_not_exist.json")


def test_validate_anchors_passes_on_complete_graph(valid_graph: dict[str, dict]) -> None:
    validate_anchors(valid_graph, REQUIRED_ANCHORS_SDXL)  # no raise


def test_validate_anchors_raises_listing_missing_anchors(valid_graph: dict[str, dict]) -> None:
    del valid_graph["4"]  # drop %KSAMPLER%
    del valid_graph["5"]  # drop %OUTPUT%
    with pytest.raises(WorkflowValidationError) as exc:
        validate_anchors(valid_graph, REQUIRED_ANCHORS_SDXL)
    msg = str(exc.value)
    assert "%KSAMPLER%" in msg
    assert "%OUTPUT%" in msg


def test_validate_anchors_raises_on_duplicate(valid_graph: dict[str, dict]) -> None:
    # Add a second node claiming %KSAMPLER%.
    valid_graph["99"] = {"class_type": "X", "inputs": {}, "_meta": {"title": "%KSAMPLER%"}}
    with pytest.raises(WorkflowValidationError, match=r"duplicate"):
        validate_anchors(valid_graph, REQUIRED_ANCHORS_SDXL)


def test_find_anchor_returns_node_id(valid_graph: dict[str, dict]) -> None:
    assert find_anchor(valid_graph, "%KSAMPLER%") == "4"
    assert find_anchor(valid_graph, "%OUTPUT%") == "5"


def test_find_anchor_unknown_raises_key_error(valid_graph: dict[str, dict]) -> None:
    with pytest.raises(KeyError):
        find_anchor(valid_graph, "%NO_SUCH_ANCHOR%")


def test_find_anchor_honors_comma_separated_multi_anchor(valid_graph: dict[str, dict]) -> None:
    """Node 1's title is '%MODEL_SOURCE%,%CLIP_SOURCE%,%LORA_INSERT%' — all three match."""
    assert find_anchor(valid_graph, "%MODEL_SOURCE%") == "1"
    assert find_anchor(valid_graph, "%CLIP_SOURCE%") == "1"
    assert find_anchor(valid_graph, "%LORA_INSERT%") == "1"


def test_find_anchor_exact_match_not_substring(valid_graph: dict[str, dict]) -> None:
    """%MODEL% is a prefix of %MODEL_SOURCE% — must NOT match."""
    with pytest.raises(KeyError):
        find_anchor(valid_graph, "%MODEL%")


def test_real_sdxl_eps_workflow_validates() -> None:
    """The committed workflows/sdxl_eps.json must pass SDXL anchor validation."""
    graph = load_workflow(Path(__file__).parent.parent / "workflows" / "sdxl_eps.json")
    validate_anchors(graph, REQUIRED_ANCHORS_SDXL)
    # And find_anchor should resolve each required anchor to an actual node id.
    for anchor in REQUIRED_ANCHORS_SDXL:
        node_id = find_anchor(graph, anchor)
        assert node_id in graph
