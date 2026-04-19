from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth import (
    _Keyset,
    kid_for,
    load_keyset_from_env,
    parse_keys,
    require_admin,
    require_auth,
)
from app.errors import install_error_envelope

# ───────────────────────── pure helpers ─────────────────────────


def test_parse_keys_splits_trims_dedupes_drops_empty() -> None:
    result = parse_keys("  abc , def,abc,,  ghi  ,")
    assert result == frozenset({"abc", "def", "ghi"})


def test_parse_keys_empty_string_returns_empty_set() -> None:
    assert parse_keys("") == frozenset()
    assert parse_keys("   ") == frozenset()
    assert parse_keys(None) == frozenset()  # type: ignore[arg-type]


def test_kid_for_returns_sha256_prefix() -> None:
    key = "my-secret-key"
    expected = hashlib.sha256(key.encode()).hexdigest()[:8]
    assert kid_for(key) == expected
    assert len(kid_for(key)) == 8
    assert kid_for(key).islower()


def test_load_keyset_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEYS", "gen-a,gen-b")
    monkeypatch.setenv("ADMIN_API_KEYS", "admin-1")
    ks = load_keyset_from_env()
    assert ks.generation == frozenset({"gen-a", "gen-b"})
    assert ks.admin == frozenset({"admin-1"})


# ───────────────────────── FastAPI dependency matrix ─────────────────────────


def _build_app(keyset: _Keyset) -> FastAPI:
    app = FastAPI()
    app.state.keyset = keyset
    install_error_envelope(app)

    @app.get("/secured")
    async def secured(kid: str = Depends(require_auth)) -> dict:
        return {"kid": kid}

    @app.get("/admin-only")
    async def admin_only(kid: str = Depends(require_admin)) -> dict:
        return {"kid": kid}

    return app


@pytest.fixture
def keyset() -> _Keyset:
    return _Keyset(
        generation=frozenset({"gen-key"}),
        admin=frozenset({"admin-key"}),
    )


@pytest.fixture
async def app_client(keyset: _Keyset) -> AsyncIterator[AsyncClient]:
    app = _build_app(keyset)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def test_require_auth_missing_header_401(app_client: AsyncClient) -> None:
    resp = await app_client.get("/secured")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_error"


async def test_require_auth_wrong_key_401(app_client: AsyncClient) -> None:
    resp = await app_client.get("/secured", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_error"


async def test_require_auth_non_bearer_scheme_401(app_client: AsyncClient) -> None:
    resp = await app_client.get("/secured", headers={"Authorization": "Basic Z2VuLWtleQ=="})
    assert resp.status_code == 401


async def test_require_auth_case_insensitive_bearer_scheme(app_client: AsyncClient) -> None:
    """RFC 6750 §2.1: scheme names are case-insensitive."""
    for scheme in ("Bearer", "bearer", "BEARER", "BeArEr"):
        resp = await app_client.get("/secured", headers={"Authorization": f"{scheme} gen-key"})
        assert resp.status_code == 200, f"scheme={scheme!r} was rejected"


async def test_auth_error_carries_www_authenticate_header(app_client: AsyncClient) -> None:
    """RFC 7235 §3.1: 401 responses MUST carry a WWW-Authenticate challenge."""
    resp = await app_client.get("/secured")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"

    # Also on invalid token, not just missing header.
    resp = await app_client.get("/secured", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_require_auth_generation_key_ok(app_client: AsyncClient) -> None:
    resp = await app_client.get("/secured", headers={"Authorization": "Bearer gen-key"})
    assert resp.status_code == 200
    assert resp.json() == {"kid": kid_for("gen-key")}


async def test_require_auth_admin_key_accepted_on_generation_route(
    app_client: AsyncClient,
) -> None:
    resp = await app_client.get("/secured", headers={"Authorization": "Bearer admin-key"})
    assert resp.status_code == 200
    assert resp.json() == {"kid": kid_for("admin-key")}


async def test_require_admin_generation_key_forbidden_403(app_client: AsyncClient) -> None:
    resp = await app_client.get("/admin-only", headers={"Authorization": "Bearer gen-key"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "auth_error"


async def test_require_admin_admin_key_ok(app_client: AsyncClient) -> None:
    resp = await app_client.get("/admin-only", headers={"Authorization": "Bearer admin-key"})
    assert resp.status_code == 200


async def test_require_admin_missing_header_401(app_client: AsyncClient) -> None:
    resp = await app_client.get("/admin-only")
    assert resp.status_code == 401


async def test_empty_api_keys_rejects_authorized_requests() -> None:
    """Fail-closed: if API_KEYS is empty, every Authorization header is rejected."""
    empty = _Keyset(generation=frozenset(), admin=frozenset())
    app = _build_app(empty)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            resp = await c.get("/secured", headers={"Authorization": "Bearer anything"})
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == "auth_error"
