from __future__ import annotations

import json

from httpx import AsyncClient

from app.queue.jobs import create_queued, set_failed


async def test_metrics_is_public_and_prometheus_formatted(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200, resp.text
    assert "imagegen_http_requests_total" in resp.text
    assert "imagegen_queue_depth" in resp.text


async def test_admin_monitoring_requires_admin_scope(client: AsyncClient) -> None:
    unauth = await client.get("/v1/admin/monitoring/status")
    assert unauth.status_code == 401

    wrong_scope = await client.get(
        "/v1/admin/monitoring/status",
        headers={"Authorization": "Bearer test-gen-key"},
    )
    assert wrong_scope.status_code == 403


async def test_admin_monitoring_status_and_queue(client: AsyncClient) -> None:
    from app.main import app

    job = await create_queued(
        app.state.store,
        model_name="noobai-xl-v1.1",
        input_json=json.dumps({"model": "noobai-xl-v1.1"}),
        mode="sync",
    )
    await set_failed(app.state.store, job.id, error_code="monitoring_test", error_message="failed for test")

    status_resp = await client.get(
        "/v1/admin/monitoring/status",
        headers={"Authorization": "Bearer test-admin-key"},
    )
    assert status_resp.status_code == 200, status_resp.text
    status_body = status_resp.json()
    assert "runtime_bundle" in status_body
    assert "worker_last_model_name" in status_body
    assert "monitoring" in status_body
    assert "comfy_health" in status_body["monitoring"]
    assert "timeout_alert" in status_body["monitoring"]

    queue_resp = await client.get(
        "/v1/admin/monitoring/queue",
        headers={"Authorization": "Bearer test-admin-key"},
    )
    assert queue_resp.status_code == 200, queue_resp.text
    queue_body = queue_resp.json()
    assert "job_status_counts" in queue_body
    assert queue_body["job_status_counts"].get("failed", 0) >= 1
