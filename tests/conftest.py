from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Async HTTPX client wired to the FastAPI app via ASGI transport.

    Wrapped in LifespanManager so FastAPI startup/shutdown hooks actually
    fire during tests — without it, later cycles' startup validators would
    silently not run under test.
    """
    from app.main import app

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c
