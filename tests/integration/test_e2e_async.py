"""Integration test: live async submit + poll + gateway GET against compose stack.

Run:
    docker compose up -d
    uv run pytest -m integration -q tests/integration/test_e2e_async.py
"""

from __future__ import annotations

import json
import struct
import subprocess
import time
import asyncio
from pathlib import Path

import httpx
import pytest

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


def _png_dimensions(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


@pytest.fixture(scope="module", autouse=True)
def require_stack_and_async_mode() -> None:
    if not _all_healthy():
        pytest.skip("compose stack not fully healthy — run `docker compose up -d` and wait")

    # Check auth wiring first.
    health = httpx.get(
        f"{HOST_BASE_URL}/health",
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=10,
    )
    if health.status_code != 200:
        pytest.skip("service auth not configured for test key; set API_KEYS=test-gen-key")

    # Verify async feature flag is enabled on the running service.
    probe = httpx.post(
        f"{HOST_BASE_URL}/v1/images/generations",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": "noobai-xl-v1.1",
            "prompt": "async feature probe",
            "size": "512x512",
            "steps": 1,
            "mode": "async",
            "seed": 123,
        },
        timeout=20,
    )
    if probe.status_code == 400 and probe.json().get("error", {}).get("code") == "async_not_enabled":
        pytest.skip("ASYNC_MODE_ENABLED=false on running service; enable it and retry")
    if probe.status_code != 202:
        pytest.skip(f"unexpected probe response: {probe.status_code} {probe.text}")


async def test_async_submit_poll_and_fetch_png() -> None:
    async with httpx.AsyncClient(base_url=HOST_BASE_URL, timeout=180) as cli:
        submit = await cli.post(
            "/v1/images/generations",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "model": "noobai-xl-v1.1",
                "prompt": "distant skyline at sunset, soft light",
                "size": "512x512",
                "steps": 1,
                "mode": "async",
                "seed": 20260430,
            },
        )
        assert submit.status_code == 202, submit.text
        payload = submit.json()
        assert payload["status"] == "processing"
        job_id = payload["id"]
        assert submit.headers["x-job-id"] == job_id

        deadline = time.monotonic() + 120.0
        terminal: dict | None = None
        while time.monotonic() < deadline:
            poll = await cli.get(
                f"/v1/images/generations/{job_id}",
                headers={"Authorization": f"Bearer {API_KEY}"},
            )
            assert poll.status_code == 200, poll.text
            state = poll.json()
            if state["status"] in {"completed", "failed", "abandoned"}:
                terminal = state
                break
            await asyncio.sleep(0.25)

        assert terminal is not None, "poll timeout waiting for terminal state"
        assert terminal["status"] == "completed", terminal

        url = terminal["data"][0]["url"]
        assert url.startswith(HOST_BASE_URL)
        path = url[len(HOST_BASE_URL) :]

        get_resp = await cli.get(path, headers={"Authorization": f"Bearer {API_KEY}"})
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.headers["content-type"] == "image/png"
        width, height = _png_dimensions(get_resp.content)
        assert (width, height) == (512, 512)
