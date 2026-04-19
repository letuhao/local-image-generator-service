"""Integration test — requires real ComfyUI sidecar + GPU.

Run prereqs:
    docker compose build comfyui
    docker compose up -d comfyui    # wait for "healthy"
    uv run pytest -m integration -q tests/integration/test_comfyui_adapter.py

Generates a 1-step 256x256 SDXL-NoobAI PNG. Small by design so VRAM peak
stays below ~2 GB even under the host's existing pressure.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from app.backends.comfyui import ComfyUIAdapter
from app.registry.workflows import REQUIRED_ANCHORS_SDXL, load_workflow, validate_anchors

pytestmark = pytest.mark.integration

HTTP_URL = "http://127.0.0.1:8188"
WS_URL = "ws://127.0.0.1:8188/ws"
REPO_ROOT = Path(__file__).parent.parent.parent


def _comfy_is_healthy() -> bool:
    """Return True iff the comfyui compose service is in 'healthy' state."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json", "comfyui"],
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
    # docker compose ps may emit one JSON object per line for newer CLI versions.
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("Service") == "comfyui" and entry.get("Health") == "healthy":
            return True
    return False


@pytest.fixture(scope="module", autouse=True)
def require_comfyui() -> None:
    """Skip the whole module if the sidecar isn't running + healthy."""
    if not _comfy_is_healthy():
        pytest.skip("comfyui container not healthy — run `docker compose up -d comfyui` and wait")


@pytest.fixture
async def adapter() -> ComfyUIAdapter:
    a = ComfyUIAdapter(
        http_url=HTTP_URL,
        ws_url=WS_URL,
        http_timeout_s=30.0,
        poll_interval_ms=1000,
    )
    try:
        yield a
    finally:
        await a.close()


async def test_submit_and_fetch_real_png(adapter: ComfyUIAdapter) -> None:
    """Load sdxl_eps.json, override to 1-step 256x256, submit, fetch PNG bytes."""
    graph = load_workflow(REPO_ROOT / "workflows" / "sdxl_eps.json")
    validate_anchors(graph, REQUIRED_ANCHORS_SDXL)

    graph = copy.deepcopy(graph)
    # Tiny + fast: override the latent dims + KSampler steps so VRAM peak stays low.
    graph["5"]["inputs"]["width"] = 256
    graph["5"]["inputs"]["height"] = 256
    graph["6"]["inputs"]["steps"] = 1
    graph["6"]["inputs"]["seed"] = 42
    graph["3"]["inputs"]["text"] = "a simple red sphere, solid background"

    prompt_id = await adapter.submit(graph)
    assert prompt_id

    await adapter.wait_for_completion(prompt_id, timeout_s=120.0)

    outputs = await adapter.fetch_outputs(prompt_id)
    assert len(outputs) == 1
    png = outputs[0]
    assert png[:8] == b"\x89PNG\r\n\x1a\n", f"not a PNG — first 8 bytes were {png[:8]!r}"
    assert len(png) > 1024, f"PNG suspiciously small ({len(png)} bytes)"


async def test_health_and_free_on_real_comfy(adapter: ComfyUIAdapter) -> None:
    h = await adapter.health()
    assert h["status"] == "ok"
    assert h["vram_free_gb"] > 0

    # free() after the prior test should be a no-op (models already unloaded) but
    # must still succeed + return without raising.
    await adapter.free()
