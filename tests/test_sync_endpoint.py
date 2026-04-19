from __future__ import annotations

import base64
from typing import Any

import pytest
from httpx import AsyncClient

from app.backends.base import (
    ComfyNodeError,
    ComfyTimeoutError,
    ComfyUnreachableError,
)
from app.storage.s3 import StorageError

# ───────────────────────── fixtures ─────────────────────────


class _FakeAdapter:
    """Drop-in substitute for ComfyUIAdapter; swapped into app.state by fixture."""

    def __init__(self) -> None:
        self.submit_calls: list[dict] = []
        self.images: list[bytes] = [b"\x89PNG\r\n\x1a\n" + b"payload"]
        self.submit_exc: Exception | None = None
        self.wait_exc: Exception | None = None
        self.fetch_exc: Exception | None = None
        self.n_outputs: int = 1

    async def submit(self, graph: dict) -> str:
        if self.submit_exc:
            raise self.submit_exc
        self.submit_calls.append(graph)
        return "pid-test"

    async def wait_for_completion(self, prompt_id: str, timeout_s: float) -> None:
        if self.wait_exc:
            raise self.wait_exc

    async def fetch_outputs(self, prompt_id: str) -> list[bytes]:
        if self.fetch_exc:
            raise self.fetch_exc
        return self.images[: self.n_outputs]

    async def close(self) -> None:  # pragma: no cover — matches Protocol
        pass


class _FakeStorage:
    """Drop-in substitute for S3Storage; in-memory dict."""

    def __init__(self) -> None:
        self.bucket = "image-gen-test"
        self.store: dict[str, bytes] = {}
        self.upload_exc: Exception | None = None

    async def ensure_bucket(self) -> None:
        pass

    async def upload_png(self, job_id: str, index: int, data: bytes) -> tuple[str, str]:
        if self.upload_exc:
            raise self.upload_exc
        key = f"generations/test/{job_id}/{index}.png"
        self.store[key] = data
        return self.bucket, key

    async def get_object(self, bucket: str, key: str) -> bytes:
        return self.store[key]


@pytest.fixture
def fake_adapter() -> _FakeAdapter:
    return _FakeAdapter()


@pytest.fixture
def fake_storage() -> _FakeStorage:
    return _FakeStorage()


@pytest.fixture
async def client_with_fakes(
    client: AsyncClient, fake_adapter: _FakeAdapter, fake_storage: _FakeStorage
) -> AsyncClient:
    """The shared `client` fixture boots the app through lifespan; then we swap
    adapter + storage both on app.state (for GET-gateway code paths) and on the
    worker's internal refs (where the POST handler actually ends up calling them)."""
    from app.main import app

    app.state.adapter = fake_adapter
    app.state.s3 = fake_storage
    app.state.worker._adapter = fake_adapter  # type: ignore[attr-defined]
    app.state.worker._s3 = fake_storage  # type: ignore[attr-defined]
    return client


def _body(**overrides: Any) -> dict:
    base = {"model": "noobai-xl-v1.1", "prompt": "a sphere"}
    base.update(overrides)
    return base


# ───────────────────────── happy path ─────────────────────────


async def test_sync_generation_sets_response_delivered_and_handover(
    client_with_fakes: AsyncClient,
) -> None:
    """Cycle 4: normal happy path → BackgroundTask flips response_delivered=true
    AND webhook_handover=true in SQLite after the response flushes. This is the
    arch §4.8 suppress-webhook prerequisite."""
    import asyncio

    from app.main import app
    from app.queue.jobs import get_by_id

    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 200
    job_id = resp.headers["x-job-id"]

    # BackgroundTask runs after the response returns; give it a moment.
    for _ in range(20):
        row = await get_by_id(app.state.store, job_id)
        if row is not None and row.response_delivered and row.webhook_handover:
            return
        await asyncio.sleep(0.05)
    row = await get_by_id(app.state.store, job_id)
    pytest.fail(
        f"BackgroundTask did not flip flags: response_delivered="
        f"{row.response_delivered if row else None} "
        f"webhook_handover={row.webhook_handover if row else None}"
    )


async def test_sync_generation_returns_url_response(
    client_with_fakes: AsyncClient,
) -> None:
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "created" in body
    assert len(body["data"]) == 1
    assert body["data"][0]["url"].startswith("http://testserver/v1/images/gen_")
    assert body["data"][0]["url"].endswith("/0.png")
    assert resp.headers["x-job-id"].startswith("gen_")


async def test_sync_generation_b64_json_returns_inline_base64(
    client_with_fakes: AsyncClient,
) -> None:
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(response_format="b64_json"),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 200, resp.text
    entry = resp.json()["data"][0]
    assert "url" not in entry
    assert "b64_json" in entry
    decoded = base64.b64decode(entry["b64_json"])
    assert decoded.startswith(b"\x89PNG\r\n\x1a\n")


async def test_sync_generation_seed_minus_one_produces_random_seed(
    client_with_fakes: AsyncClient, fake_adapter: _FakeAdapter
) -> None:
    """seed=-1 is the OpenAI `random` sentinel. Previously we hardcoded 0 → same
    image every call. Verify the handler generates a fresh non-zero seed and
    actually puts it on the KSampler node."""
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(seed=-1),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 200, resp.text
    # The graph the adapter saw should have a concrete non-negative seed that's
    # not the hardcoded 0 sentinel.
    assert len(fake_adapter.submit_calls) == 1
    graph = fake_adapter.submit_calls[0]
    ksampler_nodes = [n for n in graph.values() if n.get("class_type") == "KSampler"]
    assert len(ksampler_nodes) == 1
    actual_seed = ksampler_nodes[0]["inputs"]["seed"]
    assert actual_seed >= 0
    # Probabilistically non-zero: secrets.randbelow(2**53) gives 0 with prob 2^-53.
    assert actual_seed != 0


async def test_sync_generation_seed_explicit_is_passed_through(
    client_with_fakes: AsyncClient, fake_adapter: _FakeAdapter
) -> None:
    """Explicit seed must be honored verbatim (not overwritten by random)."""
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(seed=4242),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 200
    graph = fake_adapter.submit_calls[0]
    ksampler_nodes = [n for n in graph.values() if n.get("class_type") == "KSampler"]
    assert ksampler_nodes[0]["inputs"]["seed"] == 4242


async def test_sync_generation_b64_json_with_n_equals_2(
    client_with_fakes: AsyncClient, fake_adapter: _FakeAdapter
) -> None:
    """Coverage gap plugged: b64_json + n=2 returns two b64 entries."""
    fake_adapter.images = [
        b"\x89PNG\r\n\x1a\n" + b"img0",
        b"\x89PNG\r\n\x1a\n" + b"img1",
    ]
    fake_adapter.n_outputs = 2
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(n=2, response_format="b64_json"),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 2
    assert all("b64_json" in entry for entry in data)
    assert all("url" not in entry for entry in data)


async def test_sync_generation_n_equals_2_returns_two_images(
    client_with_fakes: AsyncClient, fake_adapter: _FakeAdapter
) -> None:
    fake_adapter.images = [
        b"\x89PNG\r\n\x1a\n" + b"img0",
        b"\x89PNG\r\n\x1a\n" + b"img1",
    ]
    fake_adapter.n_outputs = 2
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(n=2),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 2


# ───────────────────────── auth + validation ─────────────────────────


async def test_sync_missing_auth_returns_401(client_with_fakes: AsyncClient) -> None:
    resp = await client_with_fakes.post("/v1/images/generations", json=_body())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_error"


async def test_sync_prompt_too_long_returns_400(
    client_with_fakes: AsyncClient,
) -> None:
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(prompt="x" * 8001),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"


async def test_sync_unknown_model_returns_400(client_with_fakes: AsyncClient) -> None:
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(model="no-such-model"),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"


# ───────────────────────── adapter / S3 failure surfaces ─────────────────────────


async def test_sync_empty_adapter_output_returns_500_comfy_error(
    client_with_fakes: AsyncClient, fake_adapter: _FakeAdapter
) -> None:
    """Cycle 4: zero outputs classified as `comfy_error` (arch §13)."""
    fake_adapter.images = []
    fake_adapter.n_outputs = 0
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "comfy_error"


async def test_sync_non_png_bytes_returns_500_comfy_error(
    client_with_fakes: AsyncClient, fake_adapter: _FakeAdapter
) -> None:
    """Cycle 4: non-PNG bytes classified as `comfy_error` (ComfyUI's output problem)."""
    fake_adapter.images = [b"not-a-png"]
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "comfy_error"


async def test_sync_comfy_unreachable_returns_503(
    client_with_fakes: AsyncClient, fake_adapter: _FakeAdapter
) -> None:
    fake_adapter.submit_exc = ComfyUnreachableError("down")
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "comfy_unreachable"


async def test_sync_comfy_node_error_returns_500(
    client_with_fakes: AsyncClient, fake_adapter: _FakeAdapter
) -> None:
    fake_adapter.submit_exc = ComfyNodeError("bad graph")
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "comfy_error"


async def test_sync_comfy_timeout_returns_504(
    client_with_fakes: AsyncClient, fake_adapter: _FakeAdapter
) -> None:
    fake_adapter.wait_exc = ComfyTimeoutError("timeout")
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "comfy_timeout"


async def test_lifespan_rejects_malformed_public_base_url(tmp_path, monkeypatch) -> None:
    """LOW-7: IMAGE_GEN_PUBLIC_BASE_URL without http/https scheme must fail fast."""
    import pytest
    from asgi_lifespan import LifespanManager

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "bad.db"))
    monkeypatch.setenv("IMAGE_GEN_PUBLIC_BASE_URL", "invalid-no-scheme")
    monkeypatch.setattr(
        "app.storage.s3.S3Storage.ensure_bucket",
        lambda self: _noop_async(self),
    )

    from app.main import app

    with pytest.raises(RuntimeError, match="IMAGE_GEN_PUBLIC_BASE_URL"):
        async with LifespanManager(app):
            pass


async def _noop_async(_self) -> None:
    return None


async def test_sync_storage_error_returns_502(
    client_with_fakes: AsyncClient, fake_storage: _FakeStorage
) -> None:
    fake_storage.upload_exc = StorageError("disk full")
    resp = await client_with_fakes.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "storage_error"
