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

REQUIRED_ANCHORS_FLUX: tuple[str, ...] = (
    "%MODEL_SOURCE%",
    "%CLIP_SOURCE%",
    "%LORA_INSERT%",
    "%POSITIVE_PROMPT%",
    "%NEGATIVE_PROMPT%",
    "%KSAMPLER%",
    "%OUTPUT%",
)

# Core ComfyUI node API workflows (see workflows/wan22_*_api.json).
# Replaces the former ComfyUI-WanVideoWrapper anchor set (Hướng B rebuild).
BASE_ANCHORS_WAN22: tuple[str, ...] = (
    "%WAN_T5%",
    "%WAN_POSITIVE%",
    "%WAN_NEGATIVE%",
    "%WAN_DIMS%",
    "%WAN_MODEL%",
    "%WAN_SHIFT%",
    "%WAN_VAE%",
    "%WAN_SAMPLER%",
    "%WAN_NAG_SAMPLER%",
    "%WAN_DECODE%",
    "%VIDEO_OUTPUT%",
)

# Maximum LoRAs injected via LoraLoaderModelOnly chain in core workflows.
WAN_MAX_LORAS: int = 5

# Keep for backward-compat in tests / external code that may import it.
WAN_LORA_MULTI_SLOTS: int = WAN_MAX_LORAS


def required_anchors_for_video_task(task: str) -> tuple[str, ...]:
    if task == "i2v":
        return BASE_ANCHORS_WAN22 + ("%INIT_IMAGE%",)
    if task == "t2v":
        return BASE_ANCHORS_WAN22
    raise WorkflowValidationError(f"unknown video_task for anchor validation: {task!r}")


# ComfyUI-MMAudio nodes in workflows/wan22_*_audio_api.json (with WAN anchors).
MMAUDIO_ANCHORS_WAN22: tuple[str, ...] = (
    "%MMAUDIO_FEATURE_UTILS%",
    "%MMAUDIO_DIFFUSION%",
    "%MMAUDIO_SAMPLER%",
)


def required_anchors_for_wan22_audio_task(task: str) -> tuple[str, ...]:
    """Anchors for optional-audio WAN API graphs (silent path does not include these)."""
    return required_anchors_for_video_task(task) + MMAUDIO_ANCHORS_WAN22


# ---------------------------------------------------------------------------
# LTX Video (10Eros / ltxv family) anchor sets
# ---------------------------------------------------------------------------

# Minimum required anchors shared by both t2v and i2v.
REQUIRED_ANCHORS_LTXV: tuple[str, ...] = (
    "%LTXV_CHECKPOINT%",
    "%LTXV_TEXT_ENCODER%",
    "%LTXV_VIDEO_VAE%",
    "%LTXV_POSITIVE%",
    "%LTXV_NEGATIVE%",
    "%LTXV_SEED%",
    "%LTXV_FRAMES%",
    "%LTXV_OUTPUT%",
    "%INIT_IMAGE%",
)


def required_anchors_for_ltxv_task(task: str) -> tuple[str, ...]:
    """Anchors required for LTXV workflows (both t2v and i2v use the same set)."""
    if task in ("t2v", "i2v"):
        return REQUIRED_ANCHORS_LTXV
    raise WorkflowValidationError(f"unknown video_task for ltxv anchor validation: {task!r}")


def required_anchors_for_family(family: str) -> tuple[str, ...]:
    if family == "flux":
        return REQUIRED_ANCHORS_FLUX
    return REQUIRED_ANCHORS_SDXL


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


def _basename(models_ref: str) -> str:
    """Return ComfyUI-facing filename from a `models/...` relative config ref."""
    return Path(models_ref).name


def _infer_wan_video_vae_precision(vae_basename: str) -> str | None:
    """Map VAE filename hints to ``WanVideoVAELoader`` ``precision`` choices.

    Loading e.g. ``Wan2_1_VAE_fp32.safetensors`` while leaving the graph at
    ``precision: bf16`` runs the decoder in bf16 and can yield NaNs → black
    video and VideoHelperSuite cast warnings.
    """
    n = vae_basename.lower()
    if "fp32" in n:
        return "fp32"
    if "bf16" in n:
        return "bf16"
    if "fp16" in n:
        return "fp16"
    return None


def inject_model_source(graph: dict[str, dict], *, model_cfg) -> None:
    """Inject checkpoint/vae/encoder filenames from model config into graph.

    This ensures runtime-selected model config drives execution even when the
    workflow template carries placeholder or stale source filenames.
    """
    try:
        model_source_id = find_anchor(graph, "%MODEL_SOURCE%")
        clip_source_id = find_anchor(graph, "%CLIP_SOURCE%")
    except KeyError as exc:
        raise WorkflowValidationError(f"inject_model_source: {exc}") from exc

    model_node = graph.get(model_source_id) or {}
    model_inputs = model_node.get("inputs")
    if not isinstance(model_inputs, dict):
        raise WorkflowValidationError(
            f"inject_model_source: node {model_source_id} has invalid inputs"
        )

    model_class = model_node.get("class_type")
    ckpt_name = _basename(model_cfg.checkpoint)
    if model_class == "CheckpointLoaderSimple":
        model_inputs["ckpt_name"] = ckpt_name
    elif model_class == "UnetLoaderGGUF":
        model_inputs["unet_name"] = ckpt_name
    else:
        raise WorkflowValidationError(
            f"inject_model_source: unsupported model source class {model_class!r}"
        )

    clip_node = graph.get(clip_source_id) or {}
    clip_inputs = clip_node.get("inputs")
    if not isinstance(clip_inputs, dict):
        raise WorkflowValidationError(
            f"inject_model_source: node {clip_source_id} has invalid inputs"
        )
    clip_class = clip_node.get("class_type")
    if clip_class == "DualCLIPLoader":
        if not model_cfg.clip_l or not model_cfg.t5xxl:
            raise WorkflowValidationError(
                "inject_model_source: DualCLIPLoader requires clip_l and t5xxl"
            )
        clip_inputs["clip_name1"] = _basename(model_cfg.clip_l)
        clip_inputs["clip_name2"] = _basename(model_cfg.t5xxl)
        if model_cfg.dual_clip_type:
            clip_inputs["type"] = model_cfg.dual_clip_type
    elif clip_class == "CLIPLoader":
        if not model_cfg.clip_l:
            raise WorkflowValidationError("inject_model_source: CLIPLoader requires clip_l")
        clip_inputs["clip_name"] = _basename(model_cfg.clip_l)
        te_type = (model_cfg.clip_loader_type or "").strip()
        if not te_type:
            raise WorkflowValidationError(
                "inject_model_source: CLIPLoader requires clip_loader_type (e.g. flux2)"
            )
        clip_inputs["type"] = te_type

    # Optional external VAE loader(s).
    if model_cfg.vae:
        vae_name = _basename(model_cfg.vae)
        for node in graph.values():
            if node.get("class_type") != "VAELoader":
                continue
            inputs = node.get("inputs")
            if isinstance(inputs, dict) and "vae_name" in inputs:
                inputs["vae_name"] = vae_name


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


def _infer_output_slot(
    graph: dict[str, dict],
    *,
    source_id: str,
    fallback_slot: int,
) -> int:
    """Infer which output slot downstream nodes consume from `source_id`.

    SDXL model/clip usually maps to slot 0/1 on CheckpointLoaderSimple, while
    DualCLIP-based graphs may consume clip from slot 0. If no consumers are
    present yet, return the fallback.
    """
    seen: set[int] = set()
    for _node_id, node in graph.items():
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            if (
                isinstance(value, list)
                and len(value) == 2
                and value[0] == source_id
                and isinstance(value[1], int)
            ):
                seen.add(value[1])
    if len(seen) == 1:
        return next(iter(seen))
    return fallback_slot


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

    model_source_slot = _infer_output_slot(graph, source_id=model_source_id, fallback_slot=0)
    clip_source_slot = _infer_output_slot(graph, source_id=clip_source_id, fallback_slot=1)

    chain_ids: list[str] = []
    prev_model_ref: list = [model_source_id, model_source_slot]
    prev_clip_ref: list = [clip_source_id, clip_source_slot]
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
        source_slot=model_source_slot,
        new_id=last_id,
        new_slot=0,
        skip_ids=skip,
    )
    _rewrite_inputs(
        graph,
        source_id=clip_source_id,
        source_slot=clip_source_slot,
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

def inject_video_weights(graph: dict[str, dict], *, model_cfg) -> None:
    """Fill WAN diffusion / VAE / T5 filenames from registry paths.

    Handles both core ComfyUI nodes (UNETLoader / VAELoader / CLIPLoader) and
    legacy Kijai WanVideoWrapper nodes (WanVideoModelLoader / WanVideoVAELoader /
    LoadWanVideoT5TextEncoder) by checking ``class_type`` at runtime.
    """
    try:
        model_id = find_anchor(graph, "%WAN_MODEL%")
        vae_id = find_anchor(graph, "%WAN_VAE%")
        t5_id = find_anchor(graph, "%WAN_T5%")
    except KeyError as exc:
        raise WorkflowValidationError(f"inject_video_weights: {exc}") from exc

    # --- diffusion model ---
    model_node = graph.get(model_id) or {}
    model_inputs = model_node.get("inputs")
    if not isinstance(model_inputs, dict):
        raise WorkflowValidationError(f"inject_video_weights: node {model_id} has invalid inputs")
    ckpt_basename = _basename(model_cfg.checkpoint)
    if model_node.get("class_type") == "UNETLoader":
        model_inputs["unet_name"] = ckpt_basename
    else:
        # Legacy WanVideoModelLoader or any other class with a "model" key.
        model_inputs["model"] = ckpt_basename

    # --- VAE ---
    vae_node = graph.get(vae_id) or {}
    vae_inputs = vae_node.get("inputs")
    if not isinstance(vae_inputs, dict) or not model_cfg.vae:
        raise WorkflowValidationError(
            f"inject_video_weights: node {vae_id} missing inputs or model_cfg.vae"
        )
    vae_basename = _basename(model_cfg.vae)
    if vae_node.get("class_type") == "VAELoader":
        vae_inputs["vae_name"] = vae_basename
        # Core VAELoader auto-detects dtype from file; no precision override needed.
    else:
        # Legacy WanVideoVAELoader — inject model_name and infer precision.
        vae_inputs["model_name"] = vae_basename
        if vae_node.get("class_type") == "WanVideoVAELoader":
            vae_precision = _infer_wan_video_vae_precision(vae_basename)
            if vae_precision is not None:
                vae_inputs["precision"] = vae_precision

    # --- T5 text encoder ---
    t5_node = graph.get(t5_id) or {}
    t5_inputs = t5_node.get("inputs")
    if not isinstance(t5_inputs, dict) or not model_cfg.wan_t5_encoder:
        raise WorkflowValidationError(
            f"inject_video_weights: node {t5_id} missing inputs or model_cfg.wan_t5_encoder"
        )
    t5_basename = _basename(model_cfg.wan_t5_encoder)
    if t5_node.get("class_type") == "CLIPLoader":
        t5_inputs["clip_name"] = t5_basename
    else:
        # Legacy LoadWanVideoT5TextEncoder.
        t5_inputs["model_name"] = t5_basename

    # --- optional CLIP Vision (legacy Kijai workflows only) ---
    try:
        clip_id = find_anchor(graph, "%WAN_CLIP_VISION%")
    except KeyError:
        return

    clip_node = graph.get(clip_id) or {}
    clip_inputs = clip_node.get("inputs")
    if not isinstance(clip_inputs, dict):
        raise WorkflowValidationError(f"inject_video_weights: node {clip_id} has invalid inputs")
    if model_cfg.wan_clip_vision:
        clip_inputs["clip_name"] = _basename(model_cfg.wan_clip_vision)


def inject_mmaudio_weights(graph: dict[str, dict], *, model_cfg) -> None:
    """Patch MMAudio loader filenames when audio workflow anchors are present."""
    try:
        find_anchor(graph, "%MMAUDIO_FEATURE_UTILS%")
    except KeyError:
        return

    if not (
        model_cfg.mmaudio_vae
        and model_cfg.mmaudio_synchformer
        and model_cfg.mmaudio_clip
        and model_cfg.mmaudio_diffusion
    ):
        raise WorkflowValidationError(
            "inject_mmaudio_weights: model_cfg missing mmaudio_* asset paths"
        )

    try:
        fu_id = find_anchor(graph, "%MMAUDIO_FEATURE_UTILS%")
        md_id = find_anchor(graph, "%MMAUDIO_DIFFUSION%")
    except KeyError as exc:
        raise WorkflowValidationError(f"inject_mmaudio_weights: {exc}") from exc

    fu_node = graph.get(fu_id) or {}
    fu_in = fu_node.get("inputs")
    if not isinstance(fu_in, dict):
        raise WorkflowValidationError(f"inject_mmaudio_weights: node {fu_id} has invalid inputs")
    fu_in["vae_model"] = _basename(model_cfg.mmaudio_vae)
    fu_in["synchformer_model"] = _basename(model_cfg.mmaudio_synchformer)
    fu_in["clip_model"] = _basename(model_cfg.mmaudio_clip)

    md_node = graph.get(md_id) or {}
    md_in = md_node.get("inputs")
    if not isinstance(md_in, dict):
        raise WorkflowValidationError(f"inject_mmaudio_weights: node {md_id} has invalid inputs")
    md_in["mmaudio_model"] = _basename(model_cfg.mmaudio_diffusion)


def _disconnect_wan_model_loader_lora_from_multi(graph: dict[str, dict], wan_lora_multi_id: str) -> None:
    """Drop ``lora`` input on loaders fed by ``WanVideoLoraSelectMulti`` when idle.

    The multi-slot node emits an empty WANVID list when every slot is ``none``.
    An empty WANVID list is ``[]``: ``bool([])`` is false but ``[] is not None``
    is true, so WanVideoWrapper's ``loadmodel`` uses
    ``if lora is not None`` and older builds then call ``add_lora_weights`` with no
    loop iterations yet return ``control_lora`` (UnboundLocalError).

    Omitting optional ``lora`` yields Python ``None`` and skips LoRA patching.
    """
    for nid, nd in graph.items():
        if nd.get("class_type") != "WanVideoModelLoader":
            continue
        node_in = nd.get("inputs")
        if not isinstance(node_in, dict):
            continue
        wire = node_in.get("lora")
        if not isinstance(wire, list) or len(wire) < 2:
            continue
        src = wire[0]
        if isinstance(src, (str, int)) and str(src) == str(wan_lora_multi_id):
            del node_in["lora"]


def inject_wan_lora_multi(
    graph: dict[str, dict],
    loras: Sequence[ResolvedLoraRef],
    *,
    merge_loras: bool | None = None,
    low_mem_load: bool | None = None,
) -> None:
    """Patch ``WanVideoLoraSelectMulti`` at ``%WAN_LORA_MULTI%``.

    Request order maps to ``lora_0`` … ``lora_{n-1}`` (max ``WAN_LORA_MULTI_SLOTS``).
    Unused slots stay ``none``. When ``loras`` is empty and the anchor exists,
    slots remain at template defaults (all ``none``).

    Optional ``merge_loras`` / ``low_mem_load`` override node inputs when not ``None``.
    """
    try:
        node_id = find_anchor(graph, "%WAN_LORA_MULTI%")
    except KeyError:
        if loras:
            raise WorkflowValidationError(
                "inject_wan_lora_multi: %WAN_LORA_MULTI% anchor missing"
            )
        return

    node = graph.get(node_id) or {}
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise WorkflowValidationError(f"inject_wan_lora_multi: node {node_id} has invalid inputs")

    slots = WAN_LORA_MULTI_SLOTS
    for i in range(slots):
        inputs[f"lora_{i}"] = "none"
        inputs[f"strength_{i}"] = 1.0

    for idx, ref in enumerate(loras[:slots]):
        inputs[f"lora_{idx}"] = f"{ref.name}.safetensors"
        inputs[f"strength_{idx}"] = float(ref.weight)

    if merge_loras is not None:
        inputs["merge_loras"] = merge_loras
    if low_mem_load is not None:
        inputs["low_mem_load"] = low_mem_load

    if not loras:
        _disconnect_wan_model_loader_lora_from_multi(graph, node_id)


def inject_wan_core_loras(
    graph: dict[str, dict],
    loras: Sequence[ResolvedLoraRef],
) -> None:
    """Insert ``LoraLoaderModelOnly`` chain between ``%WAN_MODEL%`` and ``%WAN_SHIFT%``.

    Core ComfyUI workflow LoRA injection (replaces ``inject_wan_lora_multi`` for
    workflows using ``UNETLoader`` + ``WanImageToVideo``).

    Strategy:
    - When ``loras`` is empty: no-op — the UNETLoader → ModelSamplingSD3 wire
      in the template stays intact.
    - When LoRAs are provided: dynamically insert ``LoraLoaderModelOnly`` nodes
      in sequence between the two anchor nodes. Each node takes the MODEL output
      of the previous node. The last node's MODEL output feeds ``%WAN_SHIFT%``.
    """
    if not loras:
        return

    try:
        model_id = find_anchor(graph, "%WAN_MODEL%")
        shift_id = find_anchor(graph, "%WAN_SHIFT%")
    except KeyError as exc:
        raise WorkflowValidationError(f"inject_wan_core_loras: {exc}") from exc

    int_keys = [int(k) for k in graph.keys() if k.isdigit()]
    next_id = max(int_keys) + 1 if int_keys else 100

    prev_model_ref: list = [model_id, 0]
    chain_ids: list[str] = []
    for lora in loras[:WAN_MAX_LORAS]:
        node_id = str(next_id)
        next_id += 1
        graph[node_id] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": list(prev_model_ref),
                "lora_name": f"{lora.name}.safetensors",
                "strength_model": float(lora.weight),
            },
            "_meta": {"title": f"wan_lora:{lora.name}"},
        }
        chain_ids.append(node_id)
        prev_model_ref = [node_id, 0]

    # Rewire ModelSamplingSD3's "model" input to the tail of the chain.
    shift_node = graph.get(shift_id) or {}
    shift_inputs = shift_node.get("inputs")
    if isinstance(shift_inputs, dict):
        shift_inputs["model"] = prev_model_ref


def inject_init_image(graph: dict[str, dict], filename: str) -> None:
    """Inject the uploaded init_image filename into the graph.
    Looks for the %INIT_IMAGE% anchor on a LoadImage node.
    """
    try:
        image_node_id = find_anchor(graph, "%INIT_IMAGE%")
        node = graph[image_node_id]
        if "inputs" not in node:
            node["inputs"] = {}
        node["inputs"]["image"] = filename
    except KeyError:
        # If the workflow doesn't support %INIT_IMAGE%, just ignore.
        pass


# ---------------------------------------------------------------------------
# LTX Video (10Eros / ltxv family) injection helpers
# ---------------------------------------------------------------------------


def inject_ltxv_weights(graph: dict[str, dict], *, model_cfg) -> None:
    """Inject checkpoint and text-encoder filenames for LTXV (10Eros) workflows.

    Patches:
    - %LTXV_CHECKPOINT% (CheckpointLoaderSimple) → ckpt_name
    - %LTXV_TEXT_ENCODER% (LTXAVTextEncoderLoader) → text_encoder + ckpt_name
    - Any LTXVAudioVAELoader node in the graph → ckpt_name (same checkpoint)
    """
    ckpt_basename = _basename(model_cfg.checkpoint)

    try:
        ckpt_id = find_anchor(graph, "%LTXV_CHECKPOINT%")
    except KeyError as exc:
        raise WorkflowValidationError(f"inject_ltxv_weights: {exc}") from exc
    ckpt_node = graph.get(ckpt_id) or {}
    ckpt_inputs = ckpt_node.get("inputs")
    if not isinstance(ckpt_inputs, dict):
        raise WorkflowValidationError(
            f"inject_ltxv_weights: node {ckpt_id} has invalid inputs"
        )
    ckpt_inputs["ckpt_name"] = ckpt_basename

    try:
        te_id = find_anchor(graph, "%LTXV_TEXT_ENCODER%")
    except KeyError as exc:
        raise WorkflowValidationError(f"inject_ltxv_weights: {exc}") from exc
    te_node = graph.get(te_id) or {}
    te_inputs = te_node.get("inputs")
    if not isinstance(te_inputs, dict):
        raise WorkflowValidationError(
            f"inject_ltxv_weights: node {te_id} has invalid inputs"
        )
    te_inputs["ckpt_name"] = ckpt_basename
    if model_cfg.ltxv_text_encoder:
        te_inputs["text_encoder"] = _basename(model_cfg.ltxv_text_encoder)

    # Patch all LTXVAudioVAELoader nodes with the standalone audio VAE file.
    # The main checkpoint (10Eros_v1_bf16) contains audio TRANSFORMER weights but
    # has no audio_vae.* weights.  Loading it as an AudioVAE gives a randomly-
    # initialised model that decodes audio latents to NaN.  LTX23_audio_vae_bf16
    # is the correct standalone audio VAE; it is made findable under the
    # "checkpoints" folder_paths group via extra_model_paths.yaml in the container.
    LTXV_AUDIO_VAE_FILENAME = "LTX23_audio_vae_bf16.safetensors"
    for node in graph.values():
        if node.get("class_type") == "LTXVAudioVAELoader":
            av_inputs = node.get("inputs")
            if isinstance(av_inputs, dict):
                av_inputs["ckpt_name"] = LTXV_AUDIO_VAE_FILENAME


def inject_ltxv_video_vae(graph: dict[str, dict], vae_name: str) -> None:
    """Inject the LTX Video VAE filename into the %LTXV_VIDEO_VAE% anchor node.

    The anchor is expected on a ``VAELoader`` node.  LTXV checkpoints do not
    embed a VAE; the video VAE must be loaded from a separate file
    (e.g. ``LTX23_video_vae_bf16.safetensors``).
    """
    try:
        vae_id = find_anchor(graph, "%LTXV_VIDEO_VAE%")
    except KeyError as exc:
        raise WorkflowValidationError(f"inject_ltxv_video_vae: {exc}") from exc
    vae_node = graph.get(vae_id) or {}
    vae_inputs = vae_node.get("inputs")
    if not isinstance(vae_inputs, dict):
        raise WorkflowValidationError(
            f"inject_ltxv_video_vae: node {vae_id} has invalid inputs"
        )
    vae_inputs["vae_name"] = vae_name


def inject_ltxv_seed(graph: dict[str, dict], actual_seed: int) -> None:
    """Inject *actual_seed* into the %LTXV_SEED% anchor node.

    The anchor is expected to be on a ``Seed (rgthree)`` node whose API
    representation exposes a ``seed`` integer input.  That seed propagates
    through SetNode/GetNode chains to RandomNoise and KSampler downstream.
    """
    try:
        seed_id = find_anchor(graph, "%LTXV_SEED%")
    except KeyError as exc:
        raise WorkflowValidationError(f"inject_ltxv_seed: {exc}") from exc
    seed_node = graph.get(seed_id) or {}
    seed_inputs = seed_node.get("inputs")
    if not isinstance(seed_inputs, dict):
        raise WorkflowValidationError(
            f"inject_ltxv_seed: node {seed_id} has invalid inputs"
        )
    # rgthree Seed node has max 1125899906842624 (2^50).
    seed_inputs["seed"] = actual_seed % 1125899906842624


def inject_ltxv_frames(graph: dict[str, dict], frames: int) -> None:
    """Inject *frames* (video length) into the %LTXV_FRAMES% anchor node.

    The anchor is expected on a ``PrimitiveInt`` node whose ``value`` feeds
    the SetNode('length_0') → GetNode('length_0') → EmptyLTXVLatentVideo chain.
    """
    try:
        frames_id = find_anchor(graph, "%LTXV_FRAMES%")
    except KeyError as exc:
        raise WorkflowValidationError(f"inject_ltxv_frames: {exc}") from exc
    frames_node = graph.get(frames_id) or {}
    frames_inputs = frames_node.get("inputs")
    if not isinstance(frames_inputs, dict):
        raise WorkflowValidationError(
            f"inject_ltxv_frames: node {frames_id} has invalid inputs"
        )
    frames_inputs["value"] = int(frames)

