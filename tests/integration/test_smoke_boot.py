"""Integration: prod-mode boot smoke and misconfiguration failure assertions."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).parent.parent.parent
HEALTH_URL = "http://127.0.0.1:8700/health"


def _run_compose(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _run_docker(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.mark.integration
def test_prod_mode_boot_health_and_broken_config_fails() -> None:
    if os.environ.get("RUN_SMOKE_BOOT_TEST", "").lower() != "true":
        pytest.skip("set RUN_SMOKE_BOOT_TEST=true to run compose boot smoke")

    env = os.environ.copy()
    env.update(
        {
            "IMAGEGEN_ENV": "prod",
            "API_KEYS": env.get("API_KEYS", "test-gen-key"),
            "WEBHOOK_ALLOWED_HOSTS": "example.com",
            "WEBHOOK_SIGNING_SECRETS": "abc123",
            "WEBHOOK_ALLOW_ANY_HOST": "false",
            "ASYNC_MODE_ENABLED": "true",
        }
    )

    up = _run_compose(["up", "-d", "--build"], env)
    assert up.returncode == 0, up.stderr

    try:
        deadline = time.monotonic() + 120
        last_error = ""
        while time.monotonic() < deadline:
            try:
                response = httpx.get(HEALTH_URL, timeout=3)
                if response.status_code == 200:
                    break
                last_error = f"status={response.status_code}"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(2)
        else:
            pytest.fail(f"/health did not become ready in 120s: {last_error}")

        broken_env = dict(env)
        broken_env["WEBHOOK_ALLOWED_HOSTS"] = ""
        broken = _run_compose(["up", "-d", "--force-recreate", "image-gen-service"], broken_env)
        assert broken.returncode == 0, broken.stderr

        cid = _run_compose(["ps", "-q", "image-gen-service"], broken_env)
        assert cid.returncode == 0, cid.stderr
        container_id = cid.stdout.strip()
        assert container_id, "missing container id for image-gen-service"

        deadline = time.monotonic() + 30
        exit_code = None
        while time.monotonic() < deadline:
            inspect = _run_docker(
                ["inspect", "--format", "{{.State.Running}} {{.State.ExitCode}}", container_id],
                broken_env,
            )
            assert inspect.returncode == 0, inspect.stderr
            parts = inspect.stdout.strip().split()
            if len(parts) >= 2 and parts[0] == "false":
                exit_code = int(parts[1])
                break
            time.sleep(1)
        assert exit_code is not None, "misconfigured container did not exit"
        assert exit_code != 0, "misconfigured container exited with success code"

        logs = _run_docker(["logs", container_id], broken_env)
        combined = f"{logs.stdout}\n{logs.stderr}"
        assert "startup_failed" in combined
    finally:
        _run_compose(["down", "-v"], env)
