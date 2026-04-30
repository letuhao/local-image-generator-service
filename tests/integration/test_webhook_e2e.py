"""Integration: async request with webhook receives signed terminal callback."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

pytestmark = pytest.mark.integration

HOST_BASE_URL = "http://127.0.0.1:8700"
API_KEY = "test-gen-key"
REPO_ROOT = Path(__file__).parent.parent.parent
RECEIVER_PORT = 18089


def _compose_env() -> dict[str, str]:
    result = subprocess.run(
        ["docker", "inspect", "local-image-generator-service-image-gen-service-1", "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    out: dict[str, str] = {}
    if result.returncode != 0:
        return out
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out


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
def require_runtime_ready() -> None:
    if not _all_healthy():
        pytest.skip("compose stack not healthy")
    env = _compose_env()
    if env.get("API_KEYS") != API_KEY:
        pytest.skip("API_KEYS not set to integration test key")
    if env.get("ASYNC_MODE_ENABLED", "").lower() != "true":
        pytest.skip("ASYNC_MODE_ENABLED is not true in running service")
    if not env.get("WEBHOOK_SIGNING_SECRETS"):
        pytest.skip("WEBHOOK_SIGNING_SECRETS empty in running service")
    allow_any = env.get("WEBHOOK_ALLOW_ANY_HOST", "").lower() == "true"
    allowed_hosts = {h.strip() for h in env.get("WEBHOOK_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if not allow_any and "host.docker.internal" not in allowed_hosts:
        pytest.skip(
            "webhook allowlist blocks host receiver; set WEBHOOK_ALLOW_ANY_HOST=true or include host.docker.internal"
        )


@pytest.mark.asyncio
async def test_webhook_e2e_signed_delivery() -> None:
    app = FastAPI()
    deliveries: list[dict[str, str]] = []

    @app.post("/hook")
    async def hook(req: Request) -> JSONResponse:
        body = (await req.body()).decode("utf-8")
        deliveries.append(
            {
                "event": req.headers.get("X-ImageGen-Event", ""),
                "job_id": req.headers.get("X-ImageGen-Job-Id", ""),
                "signature": req.headers.get("X-ImageGen-Signature", ""),
                "body": body,
            }
        )
        return JSONResponse({"ok": True})

    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=RECEIVER_PORT,
        log_level="error",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(), name="webhook-receiver")
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                async with httpx.AsyncClient(timeout=2) as c:
                    r = await c.get(f"http://127.0.0.1:{RECEIVER_PORT}/docs")
                if r.status_code in {200, 404}:
                    break
            except Exception:
                await asyncio.sleep(0.2)

        async with httpx.AsyncClient(base_url=HOST_BASE_URL, timeout=180) as cli:
            submit = await cli.post(
                "/v1/images/generations",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={
                    "model": "noobai-xl-v1.1",
                    "prompt": "webhook e2e",
                    "size": "512x512",
                    "steps": 1,
                    "mode": "async",
                    "seed": 20260430,
                    "webhook": {"url": f"http://host.docker.internal:{RECEIVER_PORT}/hook"},
                },
            )
            assert submit.status_code == 202, submit.text
            job_id = submit.json()["id"]

            terminal = None
            poll_deadline = time.monotonic() + 120
            while time.monotonic() < poll_deadline:
                poll = await cli.get(
                    f"/v1/images/generations/{job_id}",
                    headers={"Authorization": f"Bearer {API_KEY}"},
                )
                assert poll.status_code == 200, poll.text
                state = poll.json()
                if state["status"] in {"completed", "failed", "abandoned"}:
                    terminal = state
                    if state.get("webhook_delivery_status") == "succeeded":
                        break
                await asyncio.sleep(0.25)

            assert terminal is not None
            assert terminal["status"] == "completed", terminal
            assert terminal["webhook_delivery_status"] == "succeeded", terminal

        assert deliveries, "receiver did not get any webhook delivery"
        d = deliveries[-1]
        assert d["event"] == "job.completed"
        assert d["job_id"].startswith("gen_")
        assert d["signature"].startswith("t=")
        payload = json.loads(d["body"])
        assert payload["event"] == "job.completed"
        assert payload["job"]["id"] == d["job_id"]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)

