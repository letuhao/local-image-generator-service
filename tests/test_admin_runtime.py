from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient
from httpx import ASGITransport


async def _noop_smoke(*args, **kwargs) -> None:
    return None


@pytest.fixture
async def runtime_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setattr("app.storage.s3.S3Storage.ensure_bucket", lambda self: _noop_smoke())
    monkeypatch.setattr("app.main.run_registry_smoke_tests", _noop_smoke)

    from app.main import app

    async with LifespanManager(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def test_runtime_reload_requires_admin_key(runtime_client: AsyncClient) -> None:
    resp = await runtime_client.post(
        "/v1/admin/runtime/reload-registry",
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 403


async def test_runtime_reload_registry_updates_worker(
    runtime_client: AsyncClient, monkeypatch
) -> None:
    from app.main import app

    called = {"set_registry": 0}
    orig = app.state.worker.set_registry

    def wrapped(registry):
        called["set_registry"] += 1
        return orig(registry)

    monkeypatch.setattr(app.state.worker, "set_registry", wrapped)
    resp = await runtime_client.post(
        "/v1/admin/runtime/reload-registry",
        headers={"Authorization": "Bearer test-admin-key"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "noobai-xl-v1.1" in body["models"]
    assert called["set_registry"] == 1


async def test_runtime_setup_and_close_bundle(runtime_client: AsyncClient) -> None:
    setup = await runtime_client.post(
        "/v1/admin/runtime/setup-bundle",
        headers={"Authorization": "Bearer test-admin-key"},
        json={"model_names": ["noobai-xl-v1.1"], "warmup": False},
    )
    assert setup.status_code == 200, setup.text
    setup_body = setup.json()
    assert setup_body["ok"] is True
    assert setup_body["runtime_bundle"]["active_models"] == ["noobai-xl-v1.1"]

    close = await runtime_client.post(
        "/v1/admin/runtime/close-bundle",
        headers={"Authorization": "Bearer test-admin-key"},
    )
    assert close.status_code == 200, close.text
    close_body = close.json()
    assert close_body["ok"] is True
    assert close_body["runtime_bundle"]["active_models"] == []


async def test_runtime_setup_unknown_model_returns_404(runtime_client: AsyncClient) -> None:
    resp = await runtime_client.post(
        "/v1/admin/runtime/setup-bundle",
        headers={"Authorization": "Bearer test-admin-key"},
        json={"model_names": ["missing-model"], "warmup": False},
    )
    assert resp.status_code == 404
