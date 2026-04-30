"""Cycle 8 — async POST (`202` + poll) vs feature flag."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.backends.base import ComfyUnreachableError
from app.queue.jobs import get_by_id


async def _noop_ensure_bucket(self: Any) -> None:
    return None


class _FakeAdapter:
    async def submit(self, graph: dict) -> str:
        return "pid-async"

    async def wait_for_completion(self, prompt_id: str, timeout_s: float) -> None:
        return None

    async def fetch_outputs(self, prompt_id: str) -> list[bytes]:
        return [b"\x89PNG\r\n\x1a\n" + b"x"]

    async def unload_models(self, verify_timeout_s: float = 30.0) -> bool:
        return True

    async def close(self) -> None:  # pragma: no cover
        pass


class _FakeAdapterFailSubmit(_FakeAdapter):
    async def submit(self, graph: dict) -> str:
        raise ComfyUnreachableError("simulated down")


class _FakeStorage:
    bucket = "image-gen-test"

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def ensure_bucket(self) -> None:
        pass

    async def upload_png(self, job_id: str, index: int, data: bytes) -> tuple[str, str]:
        key = f"g/{job_id}/{index}.png"
        self.store[key] = data
        return self.bucket, key

    async def get_object(self, bucket: str, key: str) -> bytes:
        return self.store[key]


@pytest.fixture
async def client_async(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("ASYNC_MODE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr("app.storage.s3.S3Storage.ensure_bucket", _noop_ensure_bucket)

    adapter = _FakeAdapter()
    storage = _FakeStorage()

    from app.main import app

    async with LifespanManager(app):
        app.state.adapter = adapter
        app.state.s3 = storage
        app.state.worker._adapter = adapter  # type: ignore[attr-defined]
        app.state.worker._s3 = storage  # type: ignore[attr-defined]

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


@pytest.fixture
async def client_async_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("ASYNC_MODE_ENABLED", "false")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jobs_disabled.db"))
    monkeypatch.setattr("app.storage.s3.S3Storage.ensure_bucket", _noop_ensure_bucket)

    from app.main import app

    async with LifespanManager(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


def _body(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "noobai-xl-v1.1",
        "prompt": "async poll test",
        "size": "512x512",
        "steps": 1,
        "seed": 7,
        "mode": "async",
    }
    base.update(overrides)
    return base


async def test_async_disabled_returns_400(client_async_disabled: AsyncClient) -> None:
    resp = await client_async_disabled.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "async_not_enabled"


async def test_async_post_returns_202_and_poll_completes(client_async: AsyncClient) -> None:
    from app.main import app

    resp = await client_async.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "processing"
    assert body["id"].startswith("gen_")
    job_id = body["id"]
    assert resp.headers["x-job-id"] == job_id
    assert job_id.startswith("gen_")

    await asyncio.sleep(0.15)
    row = await get_by_id(app.state.store, job_id)
    assert row is not None
    assert row.initial_response_delivered
    assert row.mode == "async"

    deadline = time.monotonic() + 10.0
    poll_json: dict | None = None
    while time.monotonic() < deadline:
        poll = await client_async.get(
            f"/v1/images/generations/{job_id}",
            headers={"Authorization": "Bearer test-gen-key"},
        )
        assert poll.status_code == 200
        poll_json = poll.json()
        if poll_json["status"] == "completed":
            break
        await asyncio.sleep(0.03)

    assert poll_json is not None
    assert poll_json["status"] == "completed"
    assert poll_json["webhook_delivery_status"] is None
    assert len(poll_json["data"]) == 1
    assert poll_json["data"][0]["url"] == f"http://testserver/v1/images/{job_id}/0.png"


async def test_poll_unknown_job_404(client_async: AsyncClient) -> None:
    r = await client_async.get(
        "/v1/images/generations/gen_nonexistentZZ",
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert r.status_code == 404


@pytest.fixture
async def client_async_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("ASYNC_MODE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jobs_fail.db"))
    monkeypatch.setattr("app.storage.s3.S3Storage.ensure_bucket", _noop_ensure_bucket)

    adapter = _FakeAdapterFailSubmit()
    storage = _FakeStorage()

    from app.main import app

    async with LifespanManager(app):
        app.state.adapter = adapter
        app.state.s3 = storage
        app.state.worker._adapter = adapter  # type: ignore[attr-defined]
        app.state.worker._s3 = storage  # type: ignore[attr-defined]

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def test_poll_failed_job_shape(client_async_fail: AsyncClient) -> None:
    resp = await client_async_fail.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    deadline = time.monotonic() + 10.0
    poll_json: dict | None = None
    while time.monotonic() < deadline:
        poll = await client_async_fail.get(
            f"/v1/images/generations/{job_id}",
            headers={"Authorization": "Bearer test-gen-key"},
        )
        poll_json = poll.json()
        if poll_json["status"] == "failed":
            break
        await asyncio.sleep(0.03)

    assert poll_json is not None
    assert poll_json["status"] == "failed"
    assert poll_json["error"]["code"] == "comfy_unreachable"
    assert "simulated" in poll_json["error"]["message"]
