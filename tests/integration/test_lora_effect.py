"""Integration test for Cycle 5: LoRA injection produces a visibly different image.

Prereqs:
    docker compose up -d                 # all three services healthy
    At least one addressable LoRA present in ./loras/ (any subdir OK).
    uv run pytest -m integration -q tests/integration/test_lora_effect.py

Strategy: same model + same seed, twice — once plain, once with a LoRA at
strength 0.8. Assert the resulting PNG bytes hash differ. Even a light-touch
LoRA changes enough pixels for SHA-256 to diverge at seed-level determinism.

If `./loras/` is empty or holds no addressable entries, the test self-skips
with a message pointing at the scanner output.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from app.loras.scanner import scan_loras

pytestmark = pytest.mark.integration

HOST_BASE_URL = "http://127.0.0.1:8700"
API_KEY = "test-gen-key"
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


@pytest.fixture(scope="module")
def fixture_lora_name() -> str:
    """Pick the first addressable LoRA from ./loras/. Skip if none.

    Scanner sorts by name deterministically; tests stay reproducible across runs
    even as the user adds/removes LoRAs (first-alphabetically is stable as long
    as the first entry exists)."""
    loras_root = REPO_ROOT / "loras"
    metas = scan_loras(loras_root)
    addressable = [m for m in metas if m.addressable]
    if not addressable:
        pytest.skip(
            f"no addressable LoRAs in {loras_root}; drop a .safetensors "
            "(name matching ^[A-Za-z0-9_][A-Za-z0-9_/\\-.]*$) to run this test"
        )
    return addressable[0].name


@pytest.fixture(scope="module", autouse=True)
def require_stack() -> None:
    if not _all_healthy():
        pytest.skip("compose stack not fully healthy — run `docker compose up -d`")


async def _generate(cli: httpx.AsyncClient, *, loras: list[dict] | None, seed: int = 1337) -> bytes:
    body: dict = {
        "model": "noobai-xl-v1.1",
        "prompt": "a simple red sphere, solid background",
        "size": "512x512",
        "steps": 4,
        "seed": seed,
    }
    if loras is not None:
        body["loras"] = loras
    resp = await cli.post(
        "/v1/images/generations",
        json=body,
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["data"][0]["url"]
    # Don't rely on string-slicing the host prefix — public_base_url may carry a
    # trailing slash or path prefix. Use urlparse.path + optional query.
    parsed = urlparse(url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    get_resp = await cli.get(path, headers={"Authorization": f"Bearer {API_KEY}"})
    assert get_resp.status_code == 200
    return get_resp.content


async def test_lora_changes_output_hash(fixture_lora_name: str) -> None:
    async with httpx.AsyncClient(base_url=HOST_BASE_URL, timeout=300) as cli:
        plain = await _generate(cli, loras=None)
        with_lora = await _generate(cli, loras=[{"name": fixture_lora_name, "weight": 0.8}])
        assert plain[:8] == b"\x89PNG\r\n\x1a\n"
        assert with_lora[:8] == b"\x89PNG\r\n\x1a\n"
        assert hashlib.sha256(plain).hexdigest() != hashlib.sha256(with_lora).hexdigest(), (
            f"LoRA {fixture_lora_name!r} at weight=0.8 did not change output — "
            "either injection is not live or the chosen LoRA has no effect on this prompt"
        )
