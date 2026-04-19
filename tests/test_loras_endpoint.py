from __future__ import annotations

from httpx import AsyncClient


async def test_list_loras_requires_auth(client_with_loras: AsyncClient) -> None:
    resp = await client_with_loras.get("/v1/loras")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_error"


async def test_list_loras_generation_key_works(
    client_with_loras: AsyncClient,
) -> None:
    resp = await client_with_loras.get(
        "/v1/loras", headers={"Authorization": "Bearer test-gen-key"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    names = {entry["name"] for entry in body["data"]}
    assert "style_alpha" in names
    assert "hanfu/Bai_LingMiao" in names
    assert "bad name (1)" in names


async def test_list_loras_addressable_flag_distinguishes_bad_names(
    client_with_loras: AsyncClient,
) -> None:
    resp = await client_with_loras.get(
        "/v1/loras", headers={"Authorization": "Bearer test-gen-key"}
    )
    by_name = {entry["name"]: entry for entry in resp.json()["data"]}
    assert by_name["style_alpha"]["addressable"] is True
    assert by_name["style_alpha"]["reason"] is None
    assert by_name["style_alpha"]["sha256"] == "abc"
    assert by_name["style_alpha"]["source"] == "civitai"
    assert by_name["style_alpha"]["trigger_words"] == ["anime"]
    assert by_name["style_alpha"]["sidecar_status"] == "ok"
    assert by_name["hanfu/Bai_LingMiao"]["addressable"] is True
    assert by_name["hanfu/Bai_LingMiao"]["sidecar_status"] == "missing"
    assert by_name["bad name (1)"]["addressable"] is False
    assert by_name["bad name (1)"]["reason"] is not None
    assert by_name["bad name (1)"]["sidecar_status"] == "missing"


async def test_list_loras_admin_key_also_works(
    client_with_loras: AsyncClient,
) -> None:
    resp = await client_with_loras.get(
        "/v1/loras", headers={"Authorization": "Bearer test-admin-key"}
    )
    assert resp.status_code == 200
