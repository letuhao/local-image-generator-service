from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from app.queue.jobs import get_by_id


class _SlowFakeAdapter:
    """Fake adapter that blocks wait_for_completion for a controllable delay,
    giving the test time to simulate a client disconnect."""

    def __init__(self, wait_seconds: float = 0.3) -> None:
        self.client_id = "slow-client"
        self.wait_seconds = wait_seconds
        self.submits: list[dict] = []

    async def submit(self, graph: dict) -> str:
        self.submits.append(graph)
        return f"pid-{len(self.submits)}"

    async def wait_for_completion(self, prompt_id: str, timeout_s: float) -> None:
        await asyncio.sleep(self.wait_seconds)

    async def fetch_outputs(self, prompt_id: str) -> list[bytes]:
        return [b"\x89PNG\r\n\x1a\n" + b"payload"]

    async def close(self) -> None:  # pragma: no cover
        pass


class _FakeS3:
    def __init__(self) -> None:
        self.bucket = "image-gen-test"
        self.objects: dict[str, bytes] = {}

    async def ensure_bucket(self) -> None:
        pass

    async def upload_png(self, job_id: str, index: int, data: bytes) -> tuple[str, str]:
        key = f"generations/test/{job_id}/{index}.png"
        self.objects[key] = data
        return self.bucket, key

    async def get_object(self, bucket: str, key: str) -> bytes:
        return self.objects[key]

    async def delete_object(self, bucket: str, key: str) -> None:
        self.objects.pop(key, None)


@pytest.fixture
async def client_with_slow_adapter(
    client: AsyncClient,
) -> AsyncClient:
    """Swap the app's adapter for a slow one + real S3-like fake on app.state."""
    from app.main import app

    adapter = _SlowFakeAdapter(wait_seconds=1.2)  # > disconnect poll interval
    s3 = _FakeS3()

    # QueueWorker was created in the lifespan with the real adapter/s3. Replace
    # its internal refs. Also replace app.state.* for the handler's direct reads.
    app.state.adapter = adapter
    app.state.s3 = s3
    app.state.worker._adapter = adapter  # type: ignore[attr-defined]
    app.state.worker._s3 = s3  # type: ignore[attr-defined]
    return client


def _body(**overrides: object) -> dict:
    base = {"model": "noobai-xl-v1.1", "prompt": "disc test", "size": "512x512", "steps": 1}
    base.update(overrides)
    return base


async def test_normal_request_sets_response_delivered_and_handover(
    client_with_slow_adapter: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No disconnect → background task flips both flags."""
    from starlette.requests import Request

    from app.main import app

    # Force is_disconnected to always return False so the watcher never fires.
    async def _still_connected(self: Request) -> bool:
        return False

    monkeypatch.setattr(Request, "is_disconnected", _still_connected)

    resp = await client_with_slow_adapter.post(
        "/v1/images/generations",
        json=_body(),
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.headers["x-job-id"]

    # BackgroundTasks run after the response; give them a moment.
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


async def test_disconnect_flips_mode_to_async_with_handover(
    client_with_slow_adapter: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """is_disconnected() True mid-wait → mode='async', handover=true; worker continues."""
    from starlette.requests import Request

    from app.main import app

    # Patch Request.is_disconnected to return True on first call — simulates an
    # immediate client drop. Watcher's 500ms poll triggers before the 1.2s worker
    # completes, giving the DB row time to flip to mode=async.
    orig = Request.is_disconnected

    async def fake_is_disconnected(self: Request) -> bool:
        return True

    monkeypatch.setattr(Request, "is_disconnected", fake_is_disconnected)

    try:
        await client_with_slow_adapter.post(
            "/v1/images/generations",
            json=_body(),
            headers={"Authorization": "Bearer test-gen-key"},
        )
        # With raise_app_exceptions=False in conftest, the response either completes
        # 200 (worker finished before shield saw cancel) OR is empty/500 because the
        # handler was cancelled by Starlette. Either way, the DB row reflects the
        # disconnect-triggered transition.
    except Exception:  # swallows transport quirks from test ASGI
        pass
    finally:
        monkeypatch.setattr(Request, "is_disconnected", orig)

    # The db row should show mode=async + webhook_handover=true. The worker
    # still ran and completed (or is completing); either status is acceptable,
    # the handover flag is the sentinel.
    # Scan for the most recent job (there's only one in this test's fresh DB).
    conn = await app.state.store.read()
    cursor = await conn.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 1")
    row = await cursor.fetchone()
    assert row is not None
    job_id = row[0]

    # Wait for the watcher + worker to settle.
    for _ in range(30):
        j = await get_by_id(app.state.store, job_id)
        if j is not None and j.webhook_handover and j.mode == "async":
            return
        await asyncio.sleep(0.05)

    j = await get_by_id(app.state.store, job_id)
    pytest.fail(
        f"disconnect watcher did not flip row: mode={j.mode if j else None}, "
        f"handover={j.webhook_handover if j else None}"
    )
