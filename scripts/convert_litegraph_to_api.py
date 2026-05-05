"""Convert a ComfyUI LiteGraph workflow JSON to the ComfyUI API prompt format.

Usage:
    python scripts/convert_litegraph_to_api.py \
        workflows/10Eros_10SNodes_TripleSample_I2V.json \
        workflows/10eros_i2v_api.json \
        --anchor-file scripts/10eros_anchors.json

The converter:
1. Builds a link map {link_id: (src_node_str, src_slot)}.
2. Skips disabled nodes (mode != 0).
3. For each active node, builds API inputs from wired links and widget values.
4. Applies anchor _meta.title overrides from the anchor file.

Anchor file format (JSON):
    {"<node_id_str>": "<ANCHOR_TAG>", ...}
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _build_link_map(links: list) -> dict[int, tuple[str, int]]:
    """link_id → (src_node_id_str, src_output_slot)"""
    result: dict[int, tuple[str, int]] = {}
    for link in links:
        # LiteGraph link format: [link_id, src_node, src_slot, dst_node, dst_slot, type]
        if len(link) >= 3:
            link_id, src_node, src_slot = link[0], link[1], link[2]
            result[int(link_id)] = (str(src_node), int(src_slot))
    return result


def _convert_node(node: dict, link_map: dict[int, tuple[str, int]]) -> dict:
    """Convert a single LiteGraph node to API format."""
    node_type = node.get("type", "")
    title = node.get("title") or node_type

    api_inputs: dict = {}

    inputs = node.get("inputs") or []
    widgets_values = node.get("widgets_values") or []
    is_dict_widgets = isinstance(widgets_values, dict)

    if node_type == "Seed (rgthree)":
        # Special case: rgthree Seed node has empty inputs array but seed widget.
        seed_val = widgets_values[0] if (isinstance(widgets_values, list) and widgets_values) else 0
        api_inputs["seed"] = seed_val
    elif is_dict_widgets:
        # Dict-format widgets_values (e.g. VHS_VideoCombine) — use keys directly.
        for inp in inputs:
            name = inp.get("name", "")
            link_id = inp.get("link")
            if link_id is not None and link_id in link_map:
                api_inputs[name] = list(link_map[link_id])
            # Widget dict values are added below.
        for k, v in widgets_values.items():
            if k == "videopreview":
                continue  # non-serializable UI-only state
            if k not in api_inputs:
                api_inputs[k] = v
    else:
        # List-format widgets_values — map positionally to widget inputs.
        # Some nodes (e.g. KSampler) have seed inputs that occupy TWO widget slots:
        # slot N = the seed value, slot N+1 = a "control_after_generate" string
        # ("randomize"/"fixed"/"increment"/"decrement"). The control slot is
        # UI-only and must be skipped without consuming an input name.
        SEED_CONTROL_VALUES = frozenset({"randomize", "fixed", "increment", "decrement"})

        widget_idx = 0
        for inp in inputs:
            name = inp.get("name", "")
            link_id = inp.get("link")
            has_widget = inp.get("widget") is not None

            if link_id is not None and link_id in link_map:
                # Wired input — use wire reference.
                api_inputs[name] = list(link_map[link_id])
                # If also a widget, still consume the widget slot so indexing stays aligned.
                if has_widget:
                    widget_idx += 1
                    # Skip the optional seed-control slot immediately following.
                    if (isinstance(widgets_values, list)
                            and widget_idx < len(widgets_values)
                            and widgets_values[widget_idx] in SEED_CONTROL_VALUES):
                        widget_idx += 1
            elif has_widget:
                # Widget input (no wire) — pull from positional widgets_values.
                if isinstance(widgets_values, list) and widget_idx < len(widgets_values):
                    api_inputs[name] = widgets_values[widget_idx]
                widget_idx += 1
                # Skip the optional seed-control slot immediately following.
                if (isinstance(widgets_values, list)
                        and widget_idx < len(widgets_values)
                        and widgets_values[widget_idx] in SEED_CONTROL_VALUES):
                    widget_idx += 1
            # shape=7 optional inputs with no widget and no link → skip (no value to inject).

    return {
        "class_type": node_type,
        "inputs": api_inputs,
        "_meta": {"title": title},
    }


def _resolve_virtual_nodes(
    graph: dict[str, dict],
    litegraph_nodes: list,
    bypass_map: dict[str, list],
) -> dict[str, dict]:
    """Post-process the API graph to eliminate nodes not present in ComfyUI core ≤ v0.20.1.

    Operations applied in order:

    0. **Bypassed-node passthrough** (mode=4 in LiteGraph → excluded from graph):
       downstream links that pointed at a bypassed node are redirected to that
       node's first input source, using *bypass_map* built during conversion.

    1. **Passthrough substitution**: nodes that have no ComfyUI-core equivalent
       (e.g. ``Sigmas Easing``) are removed and their first input is forwarded.

    2. **CM_FloatToInt → PrimitiveInt**: the integer value is extracted from the
       upstream ``PrimitiveFloat`` widget value in the original LiteGraph JSON.

    3. **SetNode / GetNode inline**: every input wired through a GetNode is
       redirected to the original source the matching SetNode received.
       Also redirects inputs that reference a SetNode *directly* (not via
       GetNode) — KJNodes SetNode acts as a pass-through in that case.

    4. **Removal**: SetNode, GetNode, and the passthrough nodes are deleted.
    """
    lg_by_id: dict[str, dict] = {str(n["id"]): n for n in litegraph_nodes}

    # -- Step 0: apply bypass_map for muted/bypassed LiteGraph nodes -----------
    # bypass_map: bypassed_node_id → [source_node_id, source_slot]
    def _apply_ref_map(ref_map: dict[str, list]) -> None:
        for nid, node in graph.items():
            for inp_name, inp_val in list(node.get("inputs", {}).items()):
                if isinstance(inp_val, list) and inp_val[0] in ref_map:
                    node["inputs"][inp_name] = list(ref_map[inp_val[0]])

    _apply_ref_map(bypass_map)

    # -- Step 1: passthrough-substitute nodes with no ComfyUI equivalent -------
    # Maps class_type → name of the input whose value to pass through.
    PASSTHROUGH_NODES: dict[str, str] = {
        "Sigmas Easing": "sigmas",
    }
    passthrough_map: dict[str, list] = {}
    for nid, node in list(graph.items()):
        first_input_key = PASSTHROUGH_NODES.get(node.get("class_type", ""))
        if first_input_key is None:
            continue
        src_ref = node.get("inputs", {}).get(first_input_key)
        if isinstance(src_ref, list):
            passthrough_map[nid] = list(src_ref)

    _apply_ref_map(passthrough_map)
    for nid in passthrough_map:
        del graph[nid]

    # -- Step 2: replace CM_FloatToInt with PrimitiveInt -----------------------
    for nid, node in list(graph.items()):
        if node.get("class_type") != "CM_FloatToInt":
            continue
        source_ref = node.get("inputs", {}).get("a")
        if not isinstance(source_ref, list):
            continue
        src_nid = source_ref[0]
        src_class = graph.get(src_nid, {}).get("class_type", "")
        if src_class not in ("PrimitiveFloat", "Float"):
            continue
        lg_src = lg_by_id.get(src_nid, {})
        wv = lg_src.get("widgets_values") or []
        int_val = int(wv[0]) if wv else 0
        graph[nid] = {
            "class_type": "PrimitiveInt",
            "inputs": {"value": int_val},
            "_meta": {"title": f"PrimitiveInt_{nid}"},
        }

    # -- Step 3: build SetNode name → source-ref map ---------------------------
    set_map: dict[str, list] = {}
    # Also build a direct SetNode-id → source-ref map for nodes wired directly
    # to a SetNode output (KJNodes SetNode forwards its input to its output slot).
    setnode_output_map: dict[str, list] = {}
    for nid, node in graph.items():
        if node.get("class_type") != "SetNode":
            continue
        title = node.get("_meta", {}).get("title", "")
        name = title[4:] if title.startswith("Set_") else title
        inp_vals = list(node.get("inputs", {}).values())
        if inp_vals and isinstance(inp_vals[0], list):
            src_ref = list(inp_vals[0])
            set_map[name] = src_ref
            setnode_output_map[nid] = src_ref  # direct-wire passthrough

    # -- Step 4: build GetNode id → resolved source-ref map --------------------
    get_resolution: dict[str, list] = {}
    for nid, node in graph.items():
        if node.get("class_type") != "GetNode":
            continue
        title = node.get("_meta", {}).get("title", "")
        name = title[4:] if title.startswith("Get_") else title
        base_name = re.sub(r"_\d+$", "", name)
        resolved = set_map.get(name) or set_map.get(base_name)
        if resolved:
            get_resolution[nid] = resolved

    # -- Step 5: redirect inputs pointing at GetNode OR directly at SetNode ----
    virtual_ids = {nid for nid, n in graph.items() if n.get("class_type") in ("SetNode", "GetNode")}
    combined_map = {**get_resolution, **setnode_output_map}
    for nid, node in graph.items():
        if nid in virtual_ids:
            continue
        for inp_name, inp_val in list(node.get("inputs", {}).items()):
            if isinstance(inp_val, list) and inp_val[0] in combined_map:
                node["inputs"][inp_name] = list(combined_map[inp_val[0]])

    # -- Step 6: remove SetNode / GetNode --------------------------------------
    for nid in virtual_ids:
        del graph[nid]

    # -- Step 7: redirect CheckpointLoaderSimple VAE slot (slot 2) to video VAE anchor --
    # LTXV checkpoints do not embed a VAE.  If a %LTXV_VIDEO_VAE% anchor node
    # exists in the graph we redirect every input that pointed at the checkpoint
    # node's VAE output (slot 2) to use that anchor's output (slot 0) instead.
    ckpt_anchor_id: str | None = None
    vae_anchor_id: str | None = None
    for nid, node in graph.items():
        title = node.get("_meta", {}).get("title", "")
        if title == "%LTXV_CHECKPOINT%":
            ckpt_anchor_id = nid
        elif title == "%LTXV_VIDEO_VAE%":
            vae_anchor_id = nid

    if ckpt_anchor_id and vae_anchor_id:
        for node in graph.values():
            for inp_name, inp_val in list(node.get("inputs", {}).items()):
                if isinstance(inp_val, list) and inp_val == [ckpt_anchor_id, 2]:
                    node["inputs"][inp_name] = [vae_anchor_id, 0]

    # -- Step 8: normalize path separators in all string widget values ---------
    # The LiteGraph JSON may have been saved on Windows and contain backslashes
    # in file-path widget values (e.g. lora_name).  ComfyUI on Linux expects
    # forward slashes when scanning model directories.
    for node in graph.values():
        for inp_name, inp_val in node.get("inputs", {}).items():
            if isinstance(inp_val, str) and "\\" in inp_val:
                node["inputs"][inp_name] = inp_val.replace("\\", "/")

    return graph


def convert(
    litegraph_path: str | Path,
    output_path: str | Path,
    *,
    anchors: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Convert *litegraph_path* → API format, write to *output_path*, return graph."""
    litegraph_path = Path(litegraph_path)
    output_path = Path(output_path)
    anchors = anchors or {}

    with litegraph_path.open(encoding="utf-8") as f:
        wf = json.load(f)

    links: list = wf.get("links") or []
    nodes: list = wf.get("nodes") or []
    link_map = _build_link_map(links)

    # Build bypass_map for muted/bypassed nodes (mode != 0).
    # A bypassed node passes its first input through to its first output slot.
    # downstream references to that node id are redirected to the source.
    bypass_map: dict[str, list] = {}
    for node in nodes:
        mode = node.get("mode", 0)
        if mode == 0:
            continue
        node_id = str(node["id"])
        node_inputs = node.get("inputs") or []
        if node_inputs:
            link_id = node_inputs[0].get("link")
            if link_id is not None and link_id in link_map:
                bypass_map[node_id] = list(link_map[link_id])

    # Resolve transitive bypass chains (bypassed node → another bypassed node).
    # After this, every key maps to a final non-bypassed source node.
    for key in list(bypass_map):
        ref = bypass_map[key]
        visited: set[str] = {key}
        while ref[0] in bypass_map and ref[0] not in visited:
            visited.add(ref[0])
            ref = bypass_map[ref[0]]
        bypass_map[key] = ref

    graph: dict[str, dict] = {}
    skipped_types: list[str] = []

    # Anchored nodes are force-included even when bypassed (mode != 0).
    # This allows nodes like a separate VAELoader (mode=4 in the source) to be
    # promoted into the API graph and wired up during post-processing.
    forced_node_ids: set[str] = {nid for nid in anchors if anchors[nid].startswith("%")}

    for node in nodes:
        mode = node.get("mode", 0)
        node_id = str(node["id"])
        is_forced = node_id in forced_node_ids

        if mode != 0 and not is_forced:
            # Disabled / bypassed node — exclude from API graph.
            continue

        node_type = node.get("type", "")
        if not node_type or node_type.startswith("42242"):
            # Notes / unknown custom nodes with UUID-style type names.
            skipped_types.append(node_type or "(blank)")
            continue

        api_node = _convert_node(node, link_map)

        # Apply anchor override from the anchors map.
        if node_id in anchors:
            api_node["_meta"]["title"] = anchors[node_id]

        graph[node_id] = api_node

    if skipped_types:
        print(f"  Skipped {len(skipped_types)} nodes with non-standard types: {set(skipped_types)}")

    graph = _resolve_virtual_nodes(graph, nodes, bypass_map)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)

    print(f"  Written {len(graph)} nodes → {output_path}")
    return graph


# ---------------------------------------------------------------------------
# 10Eros anchor maps (node IDs are identical in both I2V and T2V workflows)
# ---------------------------------------------------------------------------

_10EROS_ANCHORS: dict[str, str] = {
    "646": "%LTXV_CHECKPOINT%",
    "616": "%LTXV_TEXT_ENCODER%",
    "536": "%LTXV_POSITIVE%",
    "537": "%LTXV_NEGATIVE%",
    "524": "%LTXV_SEED%",
    "511": "%LTXV_FRAMES%",
    "597": "%LTXV_OUTPUT%",
    "528": "%INIT_IMAGE%",
    # Node 514 is a VAELoader (mode=4, bypassed in LiteGraph) that loads the
    # LTX Video VAE separately.  The 10Eros checkpoint does NOT embed a VAE, so
    # we force-include this node and redirect all CheckpointLoaderSimple VAE-slot
    # (slot 2) references to it instead.
    "514": "%LTXV_VIDEO_VAE%",
}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="LiteGraph workflow JSON path")
    parser.add_argument("output", help="Output API workflow JSON path")
    parser.add_argument("--anchor-file", help="Optional JSON file mapping node_id → anchor tag")
    parser.add_argument(
        "--10eros",
        action="store_true",
        dest="use_10eros",
        help="Use built-in 10Eros anchor map (node IDs 646,616,536,537,524,511,597,528)",
    )
    args = parser.parse_args()

    anchors: dict[str, str] = {}
    if args.use_10eros:
        anchors.update(_10EROS_ANCHORS)
    if args.anchor_file:
        with open(args.anchor_file, encoding="utf-8") as f:
            anchors.update(json.load(f))

    print(f"Converting {args.input} → {args.output}")
    graph = convert(args.input, args.output, anchors=anchors)
    print(f"  Total active nodes in output: {len(graph)}")

    # Verify that all expected anchors are present.
    found_anchors = {
        node["_meta"]["title"]
        for node in graph.values()
        if node.get("_meta", {}).get("title", "").startswith("%")
    }
    print(f"  Anchors found: {sorted(found_anchors)}")


if __name__ == "__main__":
    main()
