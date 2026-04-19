"""Integration test for Cycle 3 end-to-end sync: POST + GET gateway against real stack.

Run prereqs:
    docker compose up -d                 # all three services
    uv run pytest -m integration -q tests/integration/test_e2e_sync.py

Generates a 1-step small image (fast) end-to-end: API → adapter → ComfyUI → MinIO
→ GET /v1/images gateway.
"""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

HOST_BASE_URL = "http://127.0.0.1:8700"
API_KEY = "test-gen-key"  # must match API_KEYS in the running service's env
REPO_ROOT = Path(__file__).parent.parent.parent


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


@pytest.fixture(scope="module", autouse=True)
def require_stack() -> None:
    if not _all_healthy():
        pytest.skip("compose stack not fully healthy — run `docker compose up -d` and wait")
    # Verify the running service has API_KEYS configured so our Bearer works.
    resp = httpx.get(
        f"{HOST_BASE_URL}/health",
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=10,
    )
    if resp.status_code != 200 or "db" not in resp.json():
        pytest.skip(
            f"service /health not responding with verbose shape — "
            f"restart with API_KEYS={API_KEY} in host env"
        )


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Parse width/height from a PNG IHDR chunk. Bytes 16-24 of the IHDR."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


async def test_sync_post_returns_gateway_url_and_get_fetches_png() -> None:
    async with httpx.AsyncClient(base_url=HOST_BASE_URL, timeout=180) as cli:
        resp = await cli.post(
            "/v1/images/generations",
            json={
                "model": "noobai-xl-v1.1",
                "prompt": "a simple red sphere, solid background",
                "size": "512x512",
                "steps": 1,
                "seed": 42,
            },
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "created" in body
        assert len(body["data"]) == 1
        url = body["data"][0]["url"]
        assert url.startswith(HOST_BASE_URL)
        assert url.endswith("/0.png")
        assert resp.headers["x-job-id"].startswith("gen_")

        # Fetch through the gateway — URL is absolute, strip the base.
        path = url[len(HOST_BASE_URL) :]
        get_resp = await cli.get(path, headers={"Authorization": f"Bearer {API_KEY}"})
        assert get_resp.status_code == 200
        assert get_resp.headers["content-type"] == "image/png"
        assert get_resp.content[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = _png_dimensions(get_resp.content)
        assert (width, height) == (512, 512)
