"""Integration: live Chroma (GGUF + dual CLIP) through API → ComfyUI → MinIO → gateway GET.

Run:
    docker compose up -d
    uv run pytest -m integration -q tests/integration/test_chroma_runtime.py

Skips when `./models/` lacks Chroma artifacts from `config/models.yaml`, or when the
compose stack / API_KEYS do not match `test_e2e_sync`.

After other generations (e.g. NoobAI), the worker may still record the previous model
and run the strict swap unload path before Chroma; if ComfyUI does not report a
VRAM bump within 30s, the job fails. This module therefore restarts **only**
`image-gen-service` once per run so `_last_model_name` resets while ComfyUI keeps
running (`docker compose restart --no-deps image-gen-service`).
"""

from __future__ import annotations

import json
import struct
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

HOST_BASE_URL = "http://127.0.0.1:8700"
API_KEY = "test-gen-key"
REPO_ROOT = Path(__file__).parent.parent.parent

_CHROMA_WEIGHTS = (
    REPO_ROOT / "models" / "unet" / "chroma1-hd-q8.gguf",
    REPO_ROOT / "models" / "vae" / "ae.safetensors",
    REPO_ROOT / "models" / "text_encoders" / "clip_l.safetensors",
    REPO_ROOT / "models" / "text_encoders" / "t5xxl_fp8_e4m3fn.safetensors",
)


def _all_healthy() -> bool:
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0 or not result.stdout.strip():
        return False
    services_needed = {"comfyui", "minio", "image-gen-service"}
    healthy: set[str] = set()
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("Health") == "healthy":
            healthy.add(entry.get("Service"))
    return services_needed.issubset(healthy)


def _wait_stack_ready(*, timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _all_healthy():
            resp = httpx.get(
                f"{HOST_BASE_URL}/health",
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=10,
            )
            if resp.status_code == 200 and "db" in resp.json():
                return
        time.sleep(2.0)
    pytest.fail(f"stack not healthy within {timeout_s}s")


def _restart_image_gen_service() -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "restart",
            "--no-deps",
            "-t",
            "2",
            "image-gen-service",
        ],
        cwd=str(REPO_ROOT),
        timeout=180,
        check=True,
        capture_output=True,
        text=True,
    )


def _chroma_weights_present() -> bool:
    return all(p.is_file() for p in _CHROMA_WEIGHTS)


@pytest.fixture(scope="module", autouse=True)
def require_chroma_runtime() -> None:
    missing = [str(p.relative_to(REPO_ROOT)) for p in _CHROMA_WEIGHTS if not p.is_file()]
    if missing:
        pytest.skip(
            "Chroma weights not on disk — place files per config/models.yaml: "
            + ", ".join(missing)
        )
    if not _all_healthy():
        pytest.skip("compose stack not fully healthy — run `docker compose up -d` and wait")
    resp = httpx.get(
        f"{HOST_BASE_URL}/health",
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=10,
    )
    if resp.status_code != 200 or "db" not in resp.json():
        pytest.skip(
            "service /health not responding — restart with "
            f"API_KEYS={API_KEY} (see tests/integration/test_e2e_sync.py)"
        )

    try:
        _restart_image_gen_service()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"could not restart image-gen-service for clean worker state: {exc}")
    _wait_stack_ready(timeout_s=120.0)


def _png_dimensions(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


async def test_chroma_sync_generates_png_via_gateway() -> None:
    """End-to-end Chroma generation (few steps; still exercises GGUF + T5 path)."""
    async with httpx.AsyncClient(base_url=HOST_BASE_URL, timeout=420.0) as cli:
        resp = await cli.post(
            "/v1/images/generations",
            json={
                "model": "chroma-hd-q8",
                "prompt": "macro photo of dew on a leaf, shallow depth of field, morning light",
                "size": "768x768",
                "steps": 6,
                "cfg": 4.5,
                "seed": 991337,
                "sampler": "euler",
                "scheduler": "simple",
            },
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["data"]) == 1
        url = body["data"][0]["url"]
        assert url.startswith(HOST_BASE_URL)
        assert resp.headers["x-job-id"].startswith("gen_")

        path = url[len(HOST_BASE_URL) :]
        get_resp = await cli.get(path, headers={"Authorization": f"Bearer {API_KEY}"})
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.headers["content-type"] == "image/png"
        assert get_resp.content[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = _png_dimensions(get_resp.content)
        assert (w, h) == (768, 768)
