from __future__ import annotations

import json

import pytest
from httpx import AsyncClient


class _FakeStorage:
    def __init__(self) -> None:
        self.bucket = "image-gen-test"
        self.store: dict[str, bytes] = {}

    async def ensure_bucket(self) -> None:
        pass

    async def upload_png(self, job_id: str, index: int, data: bytes) -> tuple[str, str]:
        key = f"generations/test/{job_id}/{index}.png"
        self.store[key] = data
        return self.bucket, key

    async def get_object(self, bucket: str, key: str) -> bytes:
        from app.storage.s3 import StorageNotFoundError

        if key not in self.store:
            raise StorageNotFoundError(key)
        return self.store[key]


@pytest.fixture
async def gateway_client(client: AsyncClient) -> AsyncClient:
    """Inject fake storage + pre-populate a completed job in the store."""
    from app.main import app
    from app.queue.jobs import create_queued, set_completed, set_running

    storage = _FakeStorage()
    app.state.s3 = storage

    # Create a completed job with two output keys.
    job = await create_queued(
        app.state.store, model_name="noobai-xl-v1.1", input_json=json.dumps({"prompt": "x"})
    )
    await set_running(app.state.store, job.id, prompt_id="pid", client_id="cid")
    png0 = b"\x89PNG\r\n\x1a\n" + b"image-0-bytes"
    png1 = b"\x89PNG\r\n\x1a\n" + b"image-1-bytes"
    bucket, key0 = await storage.upload_png(job.id, 0, png0)
    _, key1 = await storage.upload_png(job.id, 1, png1)
    await set_completed(
        app.state.store,
        job.id,
        output_keys=[f"{bucket}/{key0}", f"{bucket}/{key1}"],
        result_json=json.dumps({"n": 2}),
    )

    app.state._test_job_id = job.id  # type: ignore[attr-defined]
    app.state._test_png0 = png0  # type: ignore[attr-defined]
    return client


async def test_get_image_returns_png_bytes(gateway_client: AsyncClient) -> None:
    from app.main import app

    job_id = app.state._test_job_id
    expected = app.state._test_png0
    resp = await gateway_client.get(
        f"/v1/images/{job_id}/0.png",
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == expected


async def test_get_image_without_auth_returns_401(
    gateway_client: AsyncClient,
) -> None:
    from app.main import app

    job_id = app.state._test_job_id
    resp = await gateway_client.get(f"/v1/images/{job_id}/0.png")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_error"


async def test_get_image_unknown_job_returns_404(gateway_client: AsyncClient) -> None:
    resp = await gateway_client.get(
        "/v1/images/gen_does_not_exist/0.png",
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_get_image_index_out_of_range_returns_404(
    gateway_client: AsyncClient,
) -> None:
    from app.main import app

    job_id = app.state._test_job_id
    resp = await gateway_client.get(
        f"/v1/images/{job_id}/99.png",
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_get_image_admin_key_also_works(gateway_client: AsyncClient) -> None:
    from app.main import app

    job_id = app.state._test_job_id
    resp = await gateway_client.get(
        f"/v1/images/{job_id}/0.png",
        headers={"Authorization": "Bearer test-admin-key"},
    )
    assert resp.status_code == 200


async def test_get_image_non_png_extension_returns_404(
    gateway_client: AsyncClient,
) -> None:
    from app.main import app

    job_id = app.state._test_job_id
    resp = await gateway_client.get(
        f"/v1/images/{job_id}/0.jpg",
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 404
