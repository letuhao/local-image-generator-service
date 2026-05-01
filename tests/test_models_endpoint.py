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
    ids = {e["id"] for e in body["data"]}
    assert "noobai-xl-v1.1" in ids
    assert "terrain-sdxl-base" in ids
    for entry in body["data"]:
        assert entry["object"] == "model"
        assert "created" in entry
        assert entry["owned_by"] == "local"
        assert entry["capabilities"].get("image_gen") is True
        assert entry["backend"] == "comfyui"
        assert entry["family"] in {"sdxl", "flux"}
        assert "description" in entry
        assert "supported_asset_types" in entry
        assert "has_confirmed_combo" in entry


async def test_list_models_admin_key_also_works(client: AsyncClient) -> None:
    resp = await client.get("/v1/models", headers={"Authorization": "Bearer test-admin-key"})
    assert resp.status_code == 200
    ids = {e["id"] for e in resp.json()["data"]}
    assert "noobai-xl-v1.1" in ids


async def test_catalog_models_lists_confirmed_presets(client: AsyncClient) -> None:
    resp = await client.get("/v1/catalog/models", headers={"Authorization": "Bearer test-gen-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    by_id = {m["id"]: m for m in body["data"]}
    terrain = by_id["terrain-sdxl-base"]
    assert "terrain-53858-v1" in terrain["confirmed_presets"]
    assert "tile_texture" in terrain["supported_asset_types"]
    assert terrain["family"] in {"sdxl", "flux"}


async def test_catalog_presets_endpoints(client: AsyncClient) -> None:
    listing = await client.get("/v1/catalog/presets", headers={"Authorization": "Bearer test-gen-key"})
    assert listing.status_code == 200
    data = listing.json()["data"]
    preset_ids = {p["id"] for p in data}
    assert "terrain-53858-v1" in preset_ids
    assert "flux-terrain-tile-draft-v1" in preset_ids
    p = next(p for p in data if p["id"] == "terrain-53858-v1")
    assert p["confirmed"] is True

    detail = await client.get(
        "/v1/catalog/presets/terrain-53858-v1",
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["bundle"]["model"] == "terrain-sdxl-base"
    assert body["generation_defaults"]["runtime_mode"] == "auto"
