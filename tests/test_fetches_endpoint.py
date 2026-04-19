from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


async def _noop_ensure_bucket(self) -> None:
    return None


@pytest.fixture
async def client_with_fetcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """App with a writable LoRA root + stubbed S3. Uses the real CivitaiFetcher
    but with respx routing HTTP calls."""
    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    monkeypatch.setenv("LORAS_ROOT", str(loras_root))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("CIVITAI_API_TOKEN", "test-token")
    monkeypatch.setattr("app.storage.s3.S3Storage.ensure_bucket", _noop_ensure_bucket)

    from app.main import app

    async with LifespanManager(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


# ── Tests ────────────────────────────────────────────────────────────


async def test_post_without_auth_401(client_with_fetcher: AsyncClient) -> None:
    resp = await client_with_fetcher.post(
        "/v1/loras/fetch",
        json={"url": "https://civitai.com/models/1?modelVersionId=2"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_error"


async def test_post_with_generation_key_403(client_with_fetcher: AsyncClient) -> None:
    resp = await client_with_fetcher.post(
        "/v1/loras/fetch",
        json={"url": "https://civitai.com/models/1?modelVersionId=2"},
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 403


async def test_post_bad_url_shape_400(client_with_fetcher: AsyncClient) -> None:
    resp = await client_with_fetcher.post(
        "/v1/loras/fetch",
        json={"url": "not a url"},
        headers={"Authorization": "Bearer test-admin-key"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"


async def test_post_happy_path_202_with_poll_url(
    client_with_fetcher: AsyncClient, tmp_path: Path
) -> None:
    payload = b"\x00" * 500
    sha = hashlib.sha256(payload).hexdigest()
    meta = {
        "id": 456,
        "modelId": 123,
        "baseModel": "SDXL 1.0",
        "trainedWords": [],
        "files": [
            {
                "primary": True,
                "name": "happy.safetensors",
                "sizeKB": 1,
                "hashes": {"SHA256": sha},
                "downloadUrl": "https://civitai.com/cdn/happy.safetensors",
            }
        ],
    }

    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        respx.get("https://civitai.com/cdn/happy.safetensors").respond(200, content=payload)

        resp = await client_with_fetcher.post(
            "/v1/loras/fetch",
            json={"url": "https://civitai.com/models/123?modelVersionId=456"},
            headers={"Authorization": "Bearer test-admin-key"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["request_id"].startswith("lfetch_")
        assert body["poll_url"] == f"/v1/loras/fetch/{body['request_id']}"
        assert body["deduped"] is False

        # Poll until done.
        import asyncio

        for _ in range(200):
            poll = await client_with_fetcher.get(
                body["poll_url"],
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert poll.status_code == 200
            if poll.json()["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.01)

    assert poll.json()["status"] == "done"
    assert poll.json()["dest_name"] == "civitai/happy_456"


async def test_get_unknown_request_id_404(
    client_with_fetcher: AsyncClient,
) -> None:
    resp = await client_with_fetcher.get(
        "/v1/loras/fetch/lfetch_nonexistent",
        headers={"Authorization": "Bearer test-admin-key"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_post_duplicate_active_version_dedupes(
    client_with_fetcher: AsyncClient,
) -> None:
    """Second POST for an in-flight version_id returns the same request_id."""
    # Slow download so the first fetch stays in-flight while we POST again.
    payload = b"\x00" * 1000
    sha = hashlib.sha256(payload).hexdigest()
    meta = {
        "files": [
            {
                "primary": True,
                "name": "dup.safetensors",
                "sizeKB": 1,
                "hashes": {"SHA256": sha},
                "downloadUrl": "https://civitai.com/cdn/dup.safetensors",
            }
        ],
    }

    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/888").respond(json=meta)

        # Delay the CDN response so the first fetch stays in flight.
        import asyncio

        release = asyncio.Event()

        async def slow_download(request: httpx.Request) -> httpx.Response:
            await release.wait()
            return httpx.Response(200, content=payload)

        respx.get("https://civitai.com/cdn/dup.safetensors").mock(side_effect=slow_download)

        first = await client_with_fetcher.post(
            "/v1/loras/fetch",
            json={"url": "https://civitai.com/models/77?modelVersionId=888"},
            headers={"Authorization": "Bearer test-admin-key"},
        )
        assert first.status_code == 202
        first_id = first.json()["request_id"]

        # Give the first fetch a moment to register + move to downloading.
        await asyncio.sleep(0.2)

        second = await client_with_fetcher.post(
            "/v1/loras/fetch",
            json={"url": "https://civitai.com/models/77?modelVersionId=888"},
            headers={"Authorization": "Bearer test-admin-key"},
        )
        assert second.status_code == 202
        body = second.json()
        assert body["request_id"] == first_id
        assert body["deduped"] is True

        release.set()
