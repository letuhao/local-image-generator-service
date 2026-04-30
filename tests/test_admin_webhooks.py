from __future__ import annotations

import json

from httpx import AsyncClient

from app.queue.jobs import (
    complete_attempt,
    create_attempt,
    create_queued,
    mark_initial_response_delivered,
    set_completed,
    set_running,
)


async def test_admin_webhook_deliveries_requires_admin_key(client: AsyncClient) -> None:
    resp = await client.get(
        "/v1/webhooks/deliveries/gen_missing",
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert resp.status_code == 403


async def test_admin_webhook_deliveries_returns_attempts(client: AsyncClient) -> None:
    from app.main import app

    store = app.state.store
    req = {"model": "noobai-xl-v1.1", "prompt": "x", "mode": "async"}
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

    attempt = await create_attempt(store, job_id=job.id, attempt_n=1, next_retry_at=None)
    await complete_attempt(
        store,
        attempt_id=attempt.id,
        status="failed",
        status_code=503,
        response_body_snippet="down",
        error=None,
        error_code=None,
        next_retry_at="2099-01-01T00:00:00+00:00",
    )

    resp = await client.get(
        f"/v1/webhooks/deliveries/{job.id}",
        headers={"Authorization": "Bearer test-admin-key"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_id"] == job.id
    assert body["webhook_url"] == "https://receiver.example/hook"
    assert len(body["attempts"]) == 1
    assert body["attempts"][0]["attempt_n"] == 1
    assert body["attempts"][0]["status"] == "failed"

