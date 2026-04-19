from __future__ import annotations

from httpx import AsyncClient


async def test_list_models_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/v1/models")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_error"


async def test_list_models_happy_path(client: AsyncClient) -> None:
    resp = await client.get("/v1/models", headers={"Authorization": "Bearer test-gen-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    entry = body["data"][0]
    assert entry["id"] == "noobai-xl-v1.1"
    assert entry["object"] == "model"
    assert "created" in entry
    assert entry["owned_by"] == "local"
    assert entry["capabilities"] == {"image_gen": True}
    assert entry["backend"] == "comfyui"


async def test_list_models_admin_key_also_works(client: AsyncClient) -> None:
    resp = await client.get("/v1/models", headers={"Authorization": "Bearer test-admin-key"})
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == "noobai-xl-v1.1"
