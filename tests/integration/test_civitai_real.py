"""Integration test for Cycle 6: real Civitai fetch through a live compose stack.

Prereqs:
    docker compose up -d --build image-gen-service   # Cycle 6 adds new env + writable mount
    CIVITAI_API_TOKEN=<token> set in the service env
    uv run pytest -m integration -q tests/integration/test_civitai_real.py

Test strategy: POST a small public-SFW LoRA URL (user picks one; the test env
var CIVITAI_TEST_URL must be set), poll the request until `done`, then verify
the file landed at the canonical path with a matching sidecar.

If CIVITAI_API_TOKEN is absent on the host OR the compose stack isn't healthy,
this test self-skips cleanly.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

HOST_BASE_URL = "http://127.0.0.1:8700"
ADMIN_KEY = "test-admin-key"
REPO_ROOT = Path(__file__).parent.parent.parent


def _stack_healthy() -> bool:
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
    needed = {"image-gen-service", "minio", "comfyui"}
    healthy: set[str] = set()
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("Health") == "healthy":
            healthy.add(entry.get("Service"))
    return needed.issubset(healthy)


@pytest.fixture(scope="module", autouse=True)
def require_stack_and_token() -> None:
    if not os.environ.get("CIVITAI_API_TOKEN"):
        pytest.skip("CIVITAI_API_TOKEN not set on host; integration test disabled")
    if not os.environ.get("CIVITAI_TEST_URL"):
        pytest.skip("CIVITAI_TEST_URL not set (paste a small public LoRA URL to enable)")
    if not _stack_healthy():
        pytest.skip("compose stack not fully healthy — run `docker compose up -d`")


async def test_civitai_fetch_lands_file_and_sidecar() -> None:
    test_url = os.environ["CIVITAI_TEST_URL"]

    async with httpx.AsyncClient(base_url=HOST_BASE_URL, timeout=300) as cli:
        resp = await cli.post(
            "/v1/loras/fetch",
            json={"url": test_url},
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()

        # Poll until terminal.
        deadline = time.monotonic() + 180
        final_status = None
        while time.monotonic() < deadline:
            poll = await cli.get(
                body["poll_url"],
                headers={"Authorization": f"Bearer {ADMIN_KEY}"},
            )
            assert poll.status_code == 200
            state = poll.json()
            if state["status"] in ("done", "failed"):
                final_status = state
                break
            import asyncio

            await asyncio.sleep(2)

        assert final_status is not None, "fetch did not terminate within 3 min"
        assert final_status["status"] == "done", final_status
        assert final_status["dest_name"] is not None

        # Verify the file appears in GET /v1/loras.
        listing = await cli.get(
            "/v1/loras",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        )
        assert listing.status_code == 200
        names = {entry["name"] for entry in listing.json()["data"]}
        assert final_status["dest_name"] in names
