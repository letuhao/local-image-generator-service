from __future__ import annotations

from httpx import AsyncClient


async def test_health_unauthenticated_boolean_shape(client: AsyncClient) -> None:
    """No Authorization header → boolean shape only (reveals no topology)."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_health_authenticated_verbose_shape_with_generation_key(
    client: AsyncClient,
) -> None:
    resp = await client.get("/health", headers={"Authorization": "Bearer test-gen-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


async def test_health_authenticated_verbose_shape_with_admin_key(
    client: AsyncClient,
) -> None:
    resp = await client.get("/health", headers={"Authorization": "Bearer test-admin-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


async def test_health_invalid_key_falls_back_to_boolean_shape(
    client: AsyncClient,
) -> None:
    """Invalid Authorization header → degrade to boolean shape (does not reveal why)."""
    resp = await client.get("/health", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_health_content_type_is_json(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")


async def test_health_supports_head(client: AsyncClient) -> None:
    resp = await client.head("/health")
    assert resp.status_code == 200
    assert resp.content == b""


async def test_health_rejects_post(client: AsyncClient) -> None:
    resp = await client.post("/health")
    assert resp.status_code == 405


async def test_health_echoes_request_id_header(client: AsyncClient) -> None:
    resp = await client.get("/health", headers={"X-Request-Id": "probe-42"})
    assert resp.status_code == 200
    assert resp.headers["x-request-id"] == "probe-42"


async def test_unknown_route_uses_error_envelope(client: AsyncClient) -> None:
    """Arch §13 requires every 4xx/5xx to carry an error code; 404s via Starlette
    must go through the envelope handler too."""
    resp = await client.get("/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"
    assert "message" in body["error"]


async def test_unhandled_exception_returns_500_envelope(client: AsyncClient) -> None:
    """Generic Python exceptions must also route through the envelope handler,
    so LoreWeave's error_code switch never sees an absent `error.code`."""
    from app.main import app

    @app.get("/__test_boom__")
    async def boom() -> dict:
        raise RuntimeError("intentional")

    try:
        resp = await client.get("/__test_boom__")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "internal"
        assert body["error"]["message"] == "Internal Server Error"
    finally:
        # Clean up the test-only route so later tests in the same session don't see it.
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) != "/__test_boom__"
        ]


async def test_health_db_unreachable_returns_503(broken_db_client: AsyncClient) -> None:
    # Unauthenticated view: boolean with degraded
    resp = await broken_db_client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded"}

    # Authenticated view: verbose with db reason
    resp = await broken_db_client.get("/health", headers={"Authorization": "Bearer test-gen-key"})
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["db"] == "unreachable"
