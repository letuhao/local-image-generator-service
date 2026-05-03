"""WAN optional graph knobs for API workflows — avoids importing validation from workflows."""

from __future__ import annotations

from typing import Any, Literal

from app.registry.workflows import WorkflowValidationError, find_anchor
from app.validation import WanVideoAdvancedOptions


def _merge_non_none(dst: dict[str, Any], patch: dict[str, Any]) -> None:
    for k, v in patch.items():
        if v is not None:
            dst[k] = v


def apply_wan_advanced_patches(
    graph: dict[str, dict],
    opts: WanVideoAdvancedOptions | None,
    *,
    video_task: Literal["t2v", "i2v"],
) -> None:
    """Apply nested ``wan_advanced`` fields to anchored WAN nodes.

    ``merge_loras`` / ``low_mem_load`` are applied via ``inject_wan_lora_multi`` in the worker,
    not here.
    """
    if opts is None:
        return

    if opts.encode is not None and video_task == "i2v":
        try:
            dims_id = find_anchor(graph, "%WAN_DIMS%")
        except KeyError:
            raise WorkflowValidationError(
                "apply_wan_advanced_patches: %WAN_DIMS% anchor missing"
            ) from None
        node = graph.get(dims_id) or {}
        if node.get("class_type") == "WanVideoImageToVideoEncode":
            # Kijai workflow — encode options apply directly.
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                raise WorkflowValidationError(
                    f"apply_wan_advanced_patches: node {dims_id} has invalid inputs"
                )
            _merge_non_none(inputs, opts.encode.model_dump(exclude_none=True))
        # Core workflow uses WanImageToVideo — encode options are silently skipped.

    if opts.clip_encode is not None and video_task == "i2v":
        try:
            clip_id = find_anchor(graph, "%WAN_CLIP_ENCODE%")
        except KeyError:
            # Core workflow has no %WAN_CLIP_ENCODE%; skip silently.
            pass
        else:
            node = graph.get(clip_id) or {}
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                raise WorkflowValidationError(
                    f"apply_wan_advanced_patches: node {clip_id} has invalid inputs"
                )
            _merge_non_none(inputs, opts.clip_encode.model_dump(exclude_none=True))

    if opts.decode is not None:
        try:
            dec_id = find_anchor(graph, "%WAN_DECODE%")
        except KeyError:
            raise WorkflowValidationError(
                "apply_wan_advanced_patches: %WAN_DECODE% anchor missing"
            ) from None
        node = graph.get(dec_id) or {}
        if node.get("class_type") == "WanVideoDecode":
            # Kijai workflow — tiling options apply.
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                raise WorkflowValidationError(
                    f"apply_wan_advanced_patches: node {dec_id} has invalid inputs"
                )
            _merge_non_none(inputs, opts.decode.model_dump(exclude_none=True))
        # Core VAEDecode has no tiling knobs; skip silently.

    if opts.block_swap is not None:
        try:
            bs_id = find_anchor(graph, "%WAN_BLOCK_SWAP%")
        except KeyError:
            # Core workflow has no block-swap node; skip silently.
            pass
        else:
            node = graph.get(bs_id) or {}
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                raise WorkflowValidationError(
                    f"apply_wan_advanced_patches: node {bs_id} has invalid inputs"
                )
            _merge_non_none(inputs, opts.block_swap.model_dump(exclude_none=True))

    if opts.export is not None:
        try:
            out_id = find_anchor(graph, "%VIDEO_OUTPUT%")
        except KeyError:
            raise WorkflowValidationError(
                "apply_wan_advanced_patches: %VIDEO_OUTPUT% anchor missing"
            ) from None
        node = graph.get(out_id) or {}
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            raise WorkflowValidationError(
                f"apply_wan_advanced_patches: node {out_id} has invalid inputs"
            )
        _merge_non_none(inputs, opts.export.model_dump(exclude_none=True))
