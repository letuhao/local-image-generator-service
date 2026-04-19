from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

# Set required env BEFORE app.main is ever imported. Values deliberately weak —
# these are test-only keys, not secrets.
os.environ.setdefault("API_KEYS", "test-gen-key,test-gen-key-2")
os.environ.setdefault("ADMIN_API_KEYS", "test-admin-key")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("LOG_PROMPTS", "false")
os.environ.setdefault("IMAGEGEN_ENV", "dev")


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """App instance with a fresh SQLite file per test."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jobs.db"))

    from app.main import app

    async with LifespanManager(app):
        # raise_app_exceptions=False: our Exception handler catches + converts to 500,
        # but httpx's default re-raises on the client side regardless. Turn that off
        # so the test sees the actual response body.
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


@pytest.fixture
async def broken_db_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """App wired to a DB path that exists but gets closed mid-test so healthcheck fails."""
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    from app.main import app

    async with LifespanManager(app):
        # Force the store into an unhealthy state by closing its connection
        # without tearing down the full lifespan. healthcheck() then returns False.
        await app.state.store.close()
        # raise_app_exceptions=False: our Exception handler catches + converts to 500,
        # but httpx's default re-raises on the client side regardless. Turn that off
        # so the test sees the actual response body.
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c
