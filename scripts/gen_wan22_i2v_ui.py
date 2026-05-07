#!/usr/bin/env python3
"""Emit workflows/wan22_i2v_ui.json — WAN 2.2 I2V LiteGraph (UI) companion to wan22_i2v_api.json.

Adds dropdowns for UNET / WAN VAE / T5 clip / model-side LoRA, plus sliders for clip
duration (seconds) and FPS. ``WanImageToVideo.length`` is driven by Math Expression
``int(a*b)+1`` (``a`` = seconds, ``b`` = FPS; same as 5×16+1→81 for defaults).

Dependencies (same Docker sidecar pins): Easy Use, Custom-Scripts Math Expression,
ComfyUI-NAG, Video Helper Suite.

Run from repo root:  python scripts/gen_wan22_i2v_ui.py
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows" / "wan22_i2v_ui.json"


def main() -> None:
    link_seq = {"n": 0}
    nodes: list[dict] = []
    links: list[list] = []
    nodes_by_id: dict[int, dict] = {}

    def add_link(src: int, src_slot: int, dst: int, dst_slot: int, typ: str) -> int:
        link_seq["n"] += 1
        lid = link_seq["n"]
        links.append([lid, src, src_slot, dst, dst_slot, typ])

        sout = nodes_by_id[src]["outputs"][src_slot]
        if sout.get("links") is None:
            sout["links"] = []
        sout["links"].append(lid)

        nodes_by_id[dst]["inputs"][dst_slot]["link"] = lid
        return lid

    def reg(node: dict) -> None:
        nodes.append(node)
        nodes_by_id[node["id"]] = node

    reg(
        {
            "id": 1,
            "type": "CLIPLoader",
            "title": "T5 CLIP",
            "pos": [80, 80],
            "size": [400, 110],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "clip_name", "name": "clip_name", "type": "COMBO", "widget": {"name": "clip_name"}, "link": None},
                {"localized_name": "type", "name": "type", "type": "COMBO", "widget": {"name": "type"}, "link": None},
                {"localized_name": "device", "name": "device", "shape": 7, "type": "COMBO", "widget": {"name": "device"}, "link": None},
            ],
            "outputs": [{"localized_name": "CLIP", "name": "CLIP", "type": "CLIP", "slot_index": 0, "links": []}],
            "widgets_values": ["umt5_xxl_fp8_e4m3fn_scaled.safetensors", "wan", "cpu"],
            "properties": {"cnr_id": "comfy-core", "ver": "0.3.46", "Node name for S&R": "CLIPLoader"},
        }
    )

    reg(
        {
            "id": 2,
            "type": "CLIPTextEncode",
            "title": "Positive prompt",
            "pos": [80, 240],
            "size": [420, 170],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "clip", "name": "clip", "type": "CLIP", "link": None},
                {"localized_name": "text", "name": "text", "type": "STRING", "widget": {"name": "text"}, "link": None},
            ],
            "outputs": [{"localized_name": "CONDITIONING", "name": "CONDITIONING", "type": "CONDITIONING", "links": []}],
            "widgets_values": [""],
            "properties": {"cnr_id": "comfy-core", "ver": "0.3.46", "Node name for S&R": "CLIPTextEncode"},
        }
    )
    reg(
        {
            "id": 3,
            "type": "CLIPTextEncode",
            "title": "Negative prompt",
            "pos": [80, 450],
            "size": [420, 170],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "clip", "name": "clip", "type": "CLIP", "link": None},
                {"localized_name": "text", "name": "text", "type": "STRING", "widget": {"name": "text"}, "link": None},
            ],
            "outputs": [{"localized_name": "CONDITIONING", "name": "CONDITIONING", "type": "CONDITIONING", "links": []}],
            "widgets_values": [""],
            "properties": {"cnr_id": "comfy-core", "ver": "0.3.46", "Node name for S&R": "CLIPTextEncode"},
        }
    )
    add_link(1, 0, 2, 0, "CLIP")
    add_link(1, 0, 3, 0, "CLIP")

    reg(
        {
            "id": 4,
            "type": "LoadImage",
            "title": "Start image",
            "pos": [80, 680],
            "size": [320, 330],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "image", "name": "image", "type": "COMBO", "widget": {"name": "image"}, "link": None},
                {"localized_name": "upload", "name": "upload", "type": "IMAGEUPLOAD", "widget": {"name": "upload"}, "link": None},
            ],
            "outputs": [
                {"localized_name": "IMAGE", "name": "IMAGE", "type": "IMAGE", "slot_index": 0, "links": []},
                {"localized_name": "MASK", "name": "MASK", "type": "MASK", "links": None},
            ],
            "widgets_values": ["placeholder.png", "image"],
            "properties": {"cnr_id": "comfy-core", "ver": "0.17.0", "Node name for S&R": "LoadImage"},
        }
    )

    reg(
        {
            "id": 5,
            "type": "UNETLoader",
            "title": "WAN diffusion (UNET weights)",
            "pos": [560, 80],
            "size": [360, 90],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "unet_name", "name": "unet_name", "type": "COMBO", "widget": {"name": "unet_name"}, "link": None},
                {"localized_name": "weight_dtype", "name": "weight_dtype", "type": "COMBO", "widget": {"name": "weight_dtype"}, "link": None},
            ],
            "outputs": [{"localized_name": "MODEL", "name": "MODEL", "type": "MODEL", "slot_index": 0, "links": []}],
            "widgets_values": ["SmoothMix_I2V_v2_High.safetensors", "fp8_e4m3fn"],
            "properties": {"cnr_id": "comfy-core", "ver": "0.3.46", "Node name for S&R": "UNETLoader"},
        }
    )

    reg(
        {
            "id": 6,
            "type": "LoraLoaderModelOnly",
            "title": "LoRA on UNet",
            "pos": [560, 210],
            "size": [420, 90],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "model", "name": "model", "type": "MODEL", "link": None},
                {"localized_name": "lora_name", "name": "lora_name", "type": "COMBO", "widget": {"name": "lora_name"}, "link": None},
                {"localized_name": "strength_model", "name": "strength_model", "type": "FLOAT", "widget": {"name": "strength_model"}, "link": None},
            ],
            "outputs": [{"localized_name": "MODEL", "name": "MODEL", "type": "MODEL", "slot_index": 0, "links": []}],
            "widgets_values": ["put_a_wan_lora_here.safetensors", 0.0],
            "properties": {"cnr_id": "comfy-core", "ver": "0.3.46", "Node name for S&R": "LoraLoaderModelOnly"},
        }
    )
    add_link(5, 0, 6, 0, "MODEL")

    reg(
        {
            "id": 7,
            "type": "ModelSamplingSD3",
            "title": "WAN shift",
            "pos": [560, 340],
            "size": [320, 90],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "model", "name": "model", "type": "MODEL", "link": None},
                {"localized_name": "shift", "name": "shift", "type": "FLOAT", "widget": {"name": "shift"}, "link": None},
            ],
            "outputs": [{"localized_name": "MODEL", "name": "MODEL", "type": "MODEL", "slot_index": 0, "links": []}],
            "widgets_values": [8.0],
            "properties": {"cnr_id": "comfy-core", "ver": "0.3.46", "Node name for S&R": "ModelSamplingSD3"},
        }
    )
    add_link(6, 0, 7, 0, "MODEL")

    reg(
        {
            "id": 8,
            "type": "VAELoader",
            "title": "WAN VAE",
            "pos": [560, 470],
            "size": [320, 60],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [{"localized_name": "vae_name", "name": "vae_name", "type": "COMBO", "widget": {"name": "vae_name"}, "link": None}],
            "outputs": [{"localized_name": "VAE", "name": "VAE", "type": "VAE", "slot_index": 0, "links": []}],
            "widgets_values": ["Wan2_1_VAE_fp32.safetensors"],
            "properties": {"cnr_id": "comfy-core", "ver": "0.3.46", "Node name for S&R": "VAELoader"},
        }
    )

    reg(
        {
            "id": 9,
            "type": "easy float",
            "title": "Duration (seconds)",
            "pos": [1040, 80],
            "size": [280, 60],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [{"localized_name": "value", "name": "value", "type": "FLOAT", "widget": {"name": "value"}, "link": None}],
            "outputs": [{"localized_name": "float", "name": "float", "type": "FLOAT", "slot_index": 0, "links": []}],
            "widgets_values": [5.0],
            "properties": {"Node name for S&R": "easy float"},
        }
    )
    reg(
        {
            "id": 10,
            "type": "easy float",
            "title": "Frames per second (video)",
            "pos": [1040, 170],
            "size": [280, 60],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [{"localized_name": "value", "name": "value", "type": "FLOAT", "widget": {"name": "value"}, "link": None}],
            "outputs": [{"localized_name": "float", "name": "float", "type": "FLOAT", "slot_index": 0, "links": []}],
            "widgets_values": [16.0],
            "properties": {"Node name for S&R": "easy float"},
        }
    )

    # One Math Expression (wired a=seconds, b=FPS). Avoid easy mathFloat: LiteGraph→API converter
    # mis-assigns widgets when multiply inputs are sockets.
    reg(
        {
            "id": 11,
            "type": "MathExpression|pysssss",
            "title": "Wan latent frames: int(seconds×FPS)+1",
            "pos": [1040, 280],
            "size": [380, 200],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "expression", "name": "expression", "type": "STRING", "widget": {"name": "expression"}, "link": None},
                {"localized_name": "a", "name": "a", "shape": 7, "type": "*", "link": None},
                {"localized_name": "b", "name": "b", "shape": 7, "type": "*", "link": None},
                {"localized_name": "c", "name": "c", "shape": 7, "type": "*", "link": None},
            ],
            "outputs": [
                {"localized_name": "INT", "name": "INT", "type": "INT", "slot_index": 0, "links": []},
                {"localized_name": "FLOAT", "name": "FLOAT", "type": "FLOAT", "slot_index": 1, "links": []},
            ],
            "widgets_values": ["int(a*b)+1"],
            "properties": {"Node name for S&R": "MathExpression|pysssss"},
        }
    )
    add_link(9, 0, 11, 1, "*")
    add_link(10, 0, 11, 2, "*")

    reg(
        {
            "id": 13,
            "type": "WanImageToVideo",
            "title": "Wan I2V (width/height/batch here; length from math)",
            "pos": [1440, 300],
            "size": [360, 280],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "positive", "name": "positive", "type": "CONDITIONING", "link": None},
                {"localized_name": "negative", "name": "negative", "type": "CONDITIONING", "link": None},
                {"localized_name": "vae", "name": "vae", "type": "VAE", "link": None},
                {"localized_name": "clip_vision_output", "name": "clip_vision_output", "shape": 7, "type": "CLIP_VISION_OUTPUT", "link": None},
                {"localized_name": "start_image", "name": "start_image", "shape": 7, "type": "IMAGE", "link": None},
                {"localized_name": "width", "name": "width", "type": "INT", "widget": {"name": "width"}, "link": None},
                {"localized_name": "height", "name": "height", "type": "INT", "widget": {"name": "height"}, "link": None},
                {"localized_name": "length", "name": "length", "type": "INT", "widget": {"name": "length"}, "link": None},
                {"localized_name": "batch_size", "name": "batch_size", "type": "INT", "widget": {"name": "batch_size"}, "link": None},
            ],
            "outputs": [
                {"localized_name": "positive", "name": "positive", "type": "CONDITIONING", "slot_index": 0, "links": []},
                {"localized_name": "negative", "name": "negative", "type": "CONDITIONING", "slot_index": 1, "links": []},
                {"localized_name": "latent", "name": "latent", "type": "LATENT", "slot_index": 2, "links": []},
            ],
            "widgets_values": [832, 480, 81, 1],
            "properties": {"cnr_id": "comfy-core", "ver": "0.3.46", "Node name for S&R": "WanImageToVideo"},
        }
    )
    add_link(2, 0, 13, 0, "CONDITIONING")
    add_link(3, 0, 13, 1, "CONDITIONING")
    add_link(8, 0, 13, 2, "VAE")
    add_link(4, 0, 13, 4, "IMAGE")
    add_link(11, 0, 13, 7, "INT")

    reg(
        {
            "id": 14,
            "type": "KSamplerAdvanced",
            "title": "Pass 1 (noise to step 3)",
            "pos": [1860, 280],
            "size": [320, 480],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "model", "name": "model", "type": "MODEL", "link": None},
                {"localized_name": "positive", "name": "positive", "type": "CONDITIONING", "link": None},
                {"localized_name": "negative", "name": "negative", "type": "CONDITIONING", "link": None},
                {"localized_name": "latent_image", "name": "latent_image", "type": "LATENT", "link": None},
                {"localized_name": "add_noise", "name": "add_noise", "type": "COMBO", "widget": {"name": "add_noise"}, "link": None},
                {"localized_name": "noise_seed", "name": "noise_seed", "type": "INT", "widget": {"name": "noise_seed"}, "link": None},
                {"localized_name": "steps", "name": "steps", "type": "INT", "widget": {"name": "steps"}, "link": None},
                {"localized_name": "cfg", "name": "cfg", "type": "FLOAT", "widget": {"name": "cfg"}, "link": None},
                {"localized_name": "sampler_name", "name": "sampler_name", "type": "COMBO", "widget": {"name": "sampler_name"}, "link": None},
                {"localized_name": "scheduler", "name": "scheduler", "type": "COMBO", "widget": {"name": "scheduler"}, "link": None},
                {"localized_name": "start_at_step", "name": "start_at_step", "type": "INT", "widget": {"name": "start_at_step"}, "link": None},
                {"localized_name": "end_at_step", "name": "end_at_step", "type": "INT", "widget": {"name": "end_at_step"}, "link": None},
                {"localized_name": "return_with_leftover_noise", "name": "return_with_leftover_noise", "type": "COMBO", "widget": {"name": "return_with_leftover_noise"}, "link": None},
            ],
            "outputs": [{"localized_name": "LATENT", "name": "LATENT", "type": "LATENT", "links": []}],
            "widgets_values": ["enable", 0, "fixed", 6, 1.0, "euler", "simple", 0, 3, "enable"],
            "properties": {"cnr_id": "comfy-core", "ver": "0.3.46", "Node name for S&R": "KSamplerAdvanced"},
        }
    )
    add_link(7, 0, 14, 0, "MODEL")
    add_link(13, 0, 14, 1, "CONDITIONING")
    add_link(13, 1, 14, 2, "CONDITIONING")
    add_link(13, 2, 14, 3, "LATENT")

    reg(
        {
            "id": 15,
            "type": "KSamplerWithNAG (Advanced)",
            "title": "Pass 2 (NAG from step 3)",
            "pos": [2220, 280],
            "size": [320, 520],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "model", "name": "model", "type": "MODEL", "link": None},
                {"localized_name": "positive", "name": "positive", "type": "CONDITIONING", "link": None},
                {"localized_name": "negative", "name": "negative", "type": "CONDITIONING", "link": None},
                {"localized_name": "nag_negative", "name": "nag_negative", "type": "CONDITIONING", "link": None},
                {"localized_name": "latent_image", "name": "latent_image", "type": "LATENT", "link": None},
                {"localized_name": "add_noise", "name": "add_noise", "type": "COMBO", "widget": {"name": "add_noise"}, "link": None},
                {"localized_name": "noise_seed", "name": "noise_seed", "type": "INT", "widget": {"name": "noise_seed"}, "link": None},
                {"localized_name": "steps", "name": "steps", "type": "INT", "widget": {"name": "steps"}, "link": None},
                {"localized_name": "cfg", "name": "cfg", "type": "FLOAT", "widget": {"name": "cfg"}, "link": None},
                {"localized_name": "nag_scale", "name": "nag_scale", "type": "FLOAT", "widget": {"name": "nag_scale"}, "link": None},
                {"localized_name": "nag_tau", "name": "nag_tau", "type": "FLOAT", "widget": {"name": "nag_tau"}, "link": None},
                {"localized_name": "nag_alpha", "name": "nag_alpha", "type": "FLOAT", "widget": {"name": "nag_alpha"}, "link": None},
                {"localized_name": "nag_sigma_end", "name": "nag_sigma_end", "type": "FLOAT", "widget": {"name": "nag_sigma_end"}, "link": None},
                {"localized_name": "sampler_name", "name": "sampler_name", "type": "COMBO", "widget": {"name": "sampler_name"}, "link": None},
                {"localized_name": "scheduler", "name": "scheduler", "type": "COMBO", "widget": {"name": "scheduler"}, "link": None},
                {"localized_name": "start_at_step", "name": "start_at_step", "type": "INT", "widget": {"name": "start_at_step"}, "link": None},
                {"localized_name": "end_at_step", "name": "end_at_step", "type": "INT", "widget": {"name": "end_at_step"}, "link": None},
                {"localized_name": "return_with_leftover_noise", "name": "return_with_leftover_noise", "type": "COMBO", "widget": {"name": "return_with_leftover_noise"}, "link": None},
            ],
            "outputs": [{"localized_name": "LATENT", "name": "LATENT", "type": "LATENT", "links": []}],
            "widgets_values": [
                "disable",
                0,
                "fixed",
                6,
                1.0,
                30,
                2.5,
                0.25,
                1.0,
                "euler",
                "simple",
                3,
                10000,
                "disable",
            ],
            "properties": {"aux_id": "scottmudge/ComfyUI-NAG", "ver": "c6f27116a8259f5b501d498a09e51c82fa72e35f", "Node name for S&R": "KSamplerWithNAG (Advanced)"},
        }
    )
    add_link(7, 0, 15, 0, "MODEL")
    add_link(13, 0, 15, 1, "CONDITIONING")
    add_link(13, 1, 15, 2, "CONDITIONING")
    add_link(13, 1, 15, 3, "CONDITIONING")
    add_link(14, 0, 15, 4, "LATENT")

    reg(
        {
            "id": 16,
            "type": "VAEDecode",
            "title": "Decode",
            "pos": [2580, 360],
            "size": [220, 46],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "samples", "name": "samples", "type": "LATENT", "link": None},
                {"localized_name": "vae", "name": "vae", "type": "VAE", "link": None},
            ],
            "outputs": [{"localized_name": "IMAGE", "name": "IMAGE", "type": "IMAGE", "slot_index": 0, "links": []}],
            "widgets_values": [],
            "properties": {"cnr_id": "comfy-core", "ver": "0.3.46", "Node name for S&R": "VAEDecode"},
        }
    )
    add_link(15, 0, 16, 0, "LATENT")
    add_link(8, 0, 16, 1, "VAE")

    vhs_widgets = {
        "frame_rate": 16.0,
        "loop_count": 0,
        "filename_prefix": "wan22_i2v_ui",
        "format": "video/h264-mp4",
        "pix_fmt": "yuv420p",
        "crf": 19,
        "save_metadata": True,
        "trim_to_audio": False,
        "pingpong": False,
        "save_output": True,
        "videopreview": {"hidden": False, "paused": False, "params": {}},
    }

    reg(
        {
            "id": 17,
            "type": "VHS_VideoCombine",
            "title": "MP4 (FPS from slider wires in)",
            "pos": [2860, 320],
            "size": [520, 360],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"localized_name": "images", "name": "images", "type": "IMAGE", "link": None},
                {"localized_name": "audio", "name": "audio", "shape": 7, "type": "AUDIO", "link": None},
                {"localized_name": "meta_batch", "name": "meta_batch", "shape": 7, "type": "VHS_BatchManager", "link": None},
                {"localized_name": "vae", "name": "vae", "shape": 7, "type": "VAE", "link": None},
                {"localized_name": "frame_rate", "name": "frame_rate", "type": "FLOAT", "widget": {"name": "frame_rate"}, "link": None},
                {"localized_name": "loop_count", "name": "loop_count", "type": "INT", "widget": {"name": "loop_count"}, "link": None},
                {"localized_name": "filename_prefix", "name": "filename_prefix", "type": "STRING", "widget": {"name": "filename_prefix"}, "link": None},
                {"localized_name": "format", "name": "format", "type": "COMBO", "widget": {"name": "format"}, "link": None},
                {"localized_name": "pingpong", "name": "pingpong", "type": "BOOLEAN", "widget": {"name": "pingpong"}, "link": None},
                {"localized_name": "save_output", "name": "save_output", "type": "BOOLEAN", "widget": {"name": "save_output"}, "link": None},
                {"localized_name": "pix_fmt", "name": "pix_fmt", "type": ["yuv420p", "yuv420p10le"], "widget": {"name": "pix_fmt"}, "link": None},
                {"localized_name": "crf", "name": "crf", "type": "INT", "widget": {"name": "crf"}, "link": None},
                {"localized_name": "save_metadata", "name": "save_metadata", "type": "BOOLEAN", "widget": {"name": "save_metadata"}, "link": None},
                {"localized_name": "trim_to_audio", "name": "trim_to_audio", "type": "BOOLEAN", "widget": {"name": "trim_to_audio"}, "link": None},
            ],
            "outputs": [{"localized_name": "Filenames", "name": "Filenames", "type": "VHS_FILENAMES", "links": None}],
            "widgets_values": vhs_widgets,
            "properties": {"cnr_id": "comfyui-videohelpersuite", "ver": "8923bd836bdab8b7bbdf4ed104b7d045e70c66e2", "Node name for S&R": "VHS_VideoCombine"},
        }
    )
    add_link(16, 0, 17, 0, "IMAGE")
    add_link(10, 0, 17, 4, "FLOAT")

    graph = {
        "id": str(uuid.uuid4()),
        "revision": 0,
        "last_node_id": max(n["id"] for n in nodes),
        "last_link_id": max(ln[0] for ln in links),
        "nodes": sorted(nodes, key=lambda x: x["id"]),
        "links": links,
        "groups": [],
        "definitions": {"subgraphs": []},
        "config": {},
        "extra": {
            "workflowRendererVersion": "LG",
            "ds": {"scale": 0.72, "offset": [-40, -20]},
            "note": (
                "WAN 2.2 I2V UI workflow — modeled on workflows/wan22_i2v_api.json. WAN uses UNETLoader (not CheckpointLoader);\n"
                "pick diffusion weights + VAE + T5 clip from the combos. Chain LoraLoaderModelOnly to patch the loaded UNet; "
                "replace the dummy lora label with any real WAN UNet-compatible LoRa (models/loras) or mute/remove the LoRA "
                "node and wire UNETLoader straight into ModelSamplingSD3 if you do not want a LoRa.\n"
                "Length sent to WanImageToVideo := int(seconds×FPS)+1. Video combine FPS reads the same FPS float — "
                "so timeline length and playback speed stay consistent with the classical 81-frame / 16-fps WAN preset."
            ),
        },
        "version": 0.4,
    }

    OUT.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(nodes)} nodes, {len(links)} links)")


if __name__ == "__main__":
    main()
