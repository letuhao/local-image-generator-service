"""init_image multipart helpers + route smoke tests."""

from __future__ import annotations

import base64
import json

import pytest
from httpx import AsyncClient

from app.api.init_image_multipart import (
    merge_init_image_upload,
    mime_from_magic,
    parse_payload_object,
)

MINI_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

AUTH = {"Authorization": "Bearer test-gen-key"}


def test_parse_payload_object_requires_dict() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_payload_object("[1,2]")


def test_merge_sets_png_data_uri_overrides_prior_init_image() -> None:
    raw = merge_init_image_upload({"model": "m", "prompt": "p", "init_image": "old"}, MINI_PNG)
    assert raw["init_image"].startswith("data:image/png;base64,")
    assert raw["model"] == "m"


def test_mime_from_magic_jpeg() -> None:
    assert mime_from_magic(b"\xff\xd8\xff\xe0") == "image/jpeg"


async def test_images_multipart_unknown_model_returns_400(client: AsyncClient) -> None:
    payload = json.dumps({"model": "no-such-model-xyz", "prompt": "hello"})
    files = {"image": ("p.png", MINI_PNG, "image/png")}
    data = {"payload": payload}
    r = await client.post(
        "/v1/images/generations/multipart",
        headers=AUTH,
        files=files,
        data=data,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


async def test_images_multipart_bad_payload_returns_400(client: AsyncClient) -> None:
    files = {"image": ("p.png", MINI_PNG, "image/png")}
    data = {"payload": "not-json"}
    r = await client.post(
        "/v1/images/generations/multipart",
        headers=AUTH,
        files=files,
        data=data,
    )
    assert r.status_code == 400


async def test_images_multipart_empty_image_returns_400(client: AsyncClient) -> None:
    payload = json.dumps({"model": "noobai-xl-v1.1", "prompt": "x"})
    files = {"image": ("empty.png", b"", "image/png")}
    data = {"payload": payload}
    r = await client.post(
        "/v1/images/generations/multipart",
        headers=AUTH,
        files=files,
        data=data,
    )
    assert r.status_code == 400


async def test_video_multipart_unknown_model_returns_400(client: AsyncClient) -> None:
    payload = json.dumps({"model": "no-such-wan-model", "prompt": "motion"})
    files = {"image": ("p.png", MINI_PNG, "image/png")}
    data = {"payload": payload}
    r = await client.post(
        "/v1/videos/generations/image-to-video/multipart",
        headers=AUTH,
        files=files,
        data=data,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"
