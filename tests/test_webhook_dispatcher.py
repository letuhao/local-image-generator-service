from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.queue.jobs import (
    complete_attempt,
    create_queued,
    create_attempt,
    get_by_id,
    get_latest_attempt,
    mark_initial_response_delivered,
    mark_response_delivered,
    set_attempt_delivering,
    set_completed,
    set_running,
)
from app.queue.store import JobStore
from app.webhooks.dispatcher import WebhookDispatcher


@pytest.mark.asyncio
async def test_dispatcher_sends_async_webhook_and_marks_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRETS", "secret-1")
    monkeypatch.setenv("WEBHOOK_ALLOW_ANY_HOST", "true")
    monkeypatch.setenv("IMAGEGEN_ENV", "dev")
    db = str(tmp_path / "jobs.db")
    store = JobStore(db)
    await store.connect()
    try:
        req = {
            "model": "noobai-xl-v1.1",
            "prompt": "x",
            "mode": "async",
            "webhook": {"url": "https://receiver.example/hook"},
        }
        job = await create_queued(
            store,
            model_name="noobai-xl-v1.1",
            input_json=json.dumps(req),
            mode="async",
            webhook_url="https://receiver.example/hook",
            webhook_headers={"X-Tenant": "a"},
        )
        await set_running(store, job.id, prompt_id="pid-1", client_id="test-client")
        await set_completed(
            store,
            job.id,
            output_keys=["image-gen/g/foo.png"],
            result_json=json.dumps({"data": [{"url": "http://testserver/v1/images/x/0.png"}]}),
        )
        await mark_initial_response_delivered(store, job.id)

        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["event"] = request.headers["X-ImageGen-Event"]
            captured["job_id"] = request.headers["X-ImageGen-Job-Id"]
            captured["sig"] = request.headers["X-ImageGen-Signature"]
            captured["tenant"] = request.headers["X-Tenant"]
            return httpx.Response(200, json={"ok": True})

        cli = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        dispatcher = WebhookDispatcher(store=store, http_client=cli)
        await dispatcher._tick()  # one-shot
        await cli.aclose()

        done = await get_by_id(store, job.id)
        assert done is not None
        assert done.webhook_delivery_status == "succeeded"
        assert captured["event"] == "job.completed"
        assert captured["job_id"] == job.id
        assert captured["tenant"] == "a"
        assert captured["sig"].startswith("t=")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatcher_suppresses_sync_when_response_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRETS", "secret-1")
    db = str(tmp_path / "jobs.db")
    store = JobStore(db)
    await store.connect()
    try:
        req = {
            "model": "noobai-xl-v1.1",
            "prompt": "x",
            "mode": "sync",
            "webhook": {"url": "https://receiver.example/hook"},
        }
        job = await create_queued(
            store,
            model_name="noobai-xl-v1.1",
            input_json=json.dumps(req),
            mode="sync",
            webhook_url="https://receiver.example/hook",
            webhook_headers=None,
        )
        await set_running(store, job.id, prompt_id="pid-1", client_id="test-client")
        await set_completed(
            store,
            job.id,
            output_keys=["image-gen/g/foo.png"],
            result_json=json.dumps({"data": [{"url": "http://testserver/v1/images/x/0.png"}]}),
        )
        await mark_response_delivered(store, job.id)

        called = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200, json={"ok": True})

        cli = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        dispatcher = WebhookDispatcher(store=store, http_client=cli)
        await dispatcher._tick()
        await cli.aclose()

        done = await get_by_id(store, job.id)
        assert done is not None
        assert done.webhook_delivery_status == "suppressed"
        assert called["n"] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatcher_retries_on_5xx_and_keeps_pending_until_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRETS", "secret-1")
    monkeypatch.setenv("WEBHOOK_ALLOW_ANY_HOST", "true")
    monkeypatch.setenv("IMAGEGEN_ENV", "dev")
    db = str(tmp_path / "jobs.db")
    store = JobStore(db)
    await store.connect()
    try:
        req = {"model": "noobai-xl-v1.1", "prompt": "x", "mode": "async"}
        job = await create_queued(
            store,
            model_name="noobai-xl-v1.1",
            input_json=json.dumps(req),
            mode="async",
            webhook_url="http://receiver.example/hook",
            webhook_headers=None,
        )
        await set_running(store, job.id, prompt_id="pid-1", client_id="test-client")
        await set_completed(
            store,
            job.id,
            output_keys=["image-gen/g/foo.png"],
            result_json=json.dumps({"data": [{"url": "http://testserver/v1/images/x/0.png"}]}),
        )
        await mark_initial_response_delivered(store, job.id)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="down")

        cli = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        dispatcher = WebhookDispatcher(store=store, http_client=cli)

        for _ in range(5):
            latest = await get_latest_attempt(store, job.id)
            if latest and latest.next_retry_at:
                await complete_attempt(
                    store,
                    attempt_id=latest.id,
                    status="failed",
                    status_code=latest.status_code,
                    response_body_snippet=latest.response_body_snippet,
                    error=latest.error,
                    error_code=latest.error_code,
                    next_retry_at="2000-01-01T00:00:00+00:00",
                )
            await dispatcher._tick()

        await cli.aclose()
        done = await get_by_id(store, job.id)
        assert done is not None
        assert done.webhook_delivery_status == "failed"
        latest = await get_latest_attempt(store, job.id)
        assert latest is not None
        assert latest.attempt_n == 5
        assert latest.next_retry_at is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatcher_toctou_blocks_when_allowlist_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRETS", "secret-1")
    monkeypatch.setenv("WEBHOOK_ALLOWED_HOSTS", "receiver.example")
    monkeypatch.setenv("WEBHOOK_ALLOW_ANY_HOST", "false")
    monkeypatch.setenv("IMAGEGEN_ENV", "dev")
    db = str(tmp_path / "jobs.db")
    store = JobStore(db)
    await store.connect()
    try:
        req = {"model": "noobai-xl-v1.1", "prompt": "x", "mode": "async"}
        job = await create_queued(
            store,
            model_name="noobai-xl-v1.1",
            input_json=json.dumps(req),
            mode="async",
            webhook_url="https://receiver.example/hook",
            webhook_headers=None,
        )
        await set_running(store, job.id, prompt_id="pid-1", client_id="test-client")
        await set_completed(
            store,
            job.id,
            output_keys=["image-gen/g/foo.png"],
            result_json=json.dumps({"data": [{"url": "http://testserver/v1/images/x/0.png"}]}),
        )
        await mark_initial_response_delivered(store, job.id)

        # Seed a due retry.
        attempt = await create_attempt(store, job_id=job.id, attempt_n=1, next_retry_at=None)
        await complete_attempt(
            store,
            attempt_id=attempt.id,
            status="failed",
            status_code=503,
            response_body_snippet="down",
            error=None,
            error_code=None,
            next_retry_at="2000-01-01T00:00:00+00:00",
        )
        monkeypatch.setenv("WEBHOOK_ALLOWED_HOSTS", "another.example")

        cli = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
        dispatcher = WebhookDispatcher(store=store, http_client=cli)
        await dispatcher._tick()
        await cli.aclose()

        done = await get_by_id(store, job.id)
        assert done is not None
        assert done.webhook_delivery_status == "failed"
        latest = await get_latest_attempt(store, job.id)
        assert latest is not None
        assert latest.error_code == "webhook_ssrf_blocked"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatcher_recovers_from_stale_delivering_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRETS", "secret-1")
    monkeypatch.setenv("WEBHOOK_ALLOW_ANY_HOST", "true")
    monkeypatch.setenv("IMAGEGEN_ENV", "dev")
    db = str(tmp_path / "jobs.db")
    store = JobStore(db)
    await store.connect()
    try:
        req = {"model": "noobai-xl-v1.1", "prompt": "x", "mode": "async"}
        job = await create_queued(
            store,
            model_name="noobai-xl-v1.1",
            input_json=json.dumps(req),
            mode="async",
            webhook_url="https://receiver.example/hook",
            webhook_headers=None,
        )
        await set_running(store, job.id, prompt_id="pid-1", client_id="test-client")
        await set_completed(
            store,
            job.id,
            output_keys=["image-gen/g/foo.png"],
            result_json=json.dumps({"data": [{"url": "http://testserver/v1/images/x/0.png"}]}),
        )
        await mark_initial_response_delivered(store, job.id)

        stale = await create_attempt(store, job_id=job.id, attempt_n=1, next_retry_at=None)
        await set_attempt_delivering(store, stale.id)

        cli = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
        dispatcher = WebhookDispatcher(store=store, http_client=cli)
        await dispatcher._tick()
        await cli.aclose()

        done = await get_by_id(store, job.id)
        assert done is not None
        assert done.webhook_delivery_status == "succeeded"
        latest = await get_latest_attempt(store, job.id)
        assert latest is not None
        assert latest.attempt_n == 2
    finally:
        await store.close()

