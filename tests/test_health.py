from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_content_type_is_json(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


async def test_health_supports_head(client: AsyncClient) -> None:
    """Load balancers commonly probe with HEAD — FastAPI auto-supports it for GET routes."""
    response = await client.head("/health")
    assert response.status_code == 200
    assert response.content == b""


async def test_health_rejects_post(client: AsyncClient) -> None:
    response = await client.post("/health")
    assert response.status_code == 405
