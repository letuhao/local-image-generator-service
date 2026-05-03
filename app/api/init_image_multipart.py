"""Merge multipart file uploads into JSON job payloads as init_image data URIs."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

MAX_INIT_IMAGE_UPLOAD_BYTES_ENV = "MAX_INIT_IMAGE_UPLOAD_BYTES"
_DEFAULT_MAX_BYTES = 25 * 1024 * 1024


def max_init_image_upload_bytes() -> int:
    raw = os.environ.get(MAX_INIT_IMAGE_UPLOAD_BYTES_ENV)
    try:
        n = int(raw) if raw is not None else _DEFAULT_MAX_BYTES
    except ValueError:
        return _DEFAULT_MAX_BYTES
    return max(1024, min(n, 200 * 1024 * 1024))


def mime_from_magic(data: bytes) -> str:
    """Sniff image MIME from magic bytes (client Content-Type is untrusted)."""
    if len(data) >= 8 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 3 and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 6 and data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError(
        "image file must be PNG, JPEG, GIF, or WebP (unrecognized magic bytes)"
    )


def parse_payload_object(payload_json: str) -> dict[str, Any]:
    """Parse the multipart `payload` form field into a dict."""
    try:
        raw = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ValueError("payload must be valid JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("payload must be a JSON object")
    return raw


def merge_init_image_upload(raw: dict[str, Any], image_bytes: bytes) -> dict[str, Any]:
    """Return a copy of `raw` with `init_image` set from uploaded bytes (data URI).

    File upload wins over any existing ``init_image`` key in ``raw``.
    """
    limit = max_init_image_upload_bytes()
    if not image_bytes:
        raise ValueError("image upload is empty")
    if len(image_bytes) > limit:
        raise ValueError(f"image exceeds max size ({limit} bytes)")
    mime = mime_from_magic(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    out = dict(raw)
    out["init_image"] = f"data:{mime};base64,{b64}"
    return out
