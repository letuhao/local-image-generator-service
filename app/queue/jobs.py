from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog
from ksuid import Ksuid

from app.queue.store import JobStore

log = structlog.get_logger(__name__)

JobStatus = Literal["queued", "running", "completed", "failed", "abandoned"]
JobMode = Literal["sync", "async"]
WebhookDeliveryStatus = Literal["pending", "succeeded", "failed", "suppressed"]

# Arch §4.2 transition table. Cycle 1 exposes the full set; Cycle 4 drives it at runtime.
_ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    "queued": frozenset({"running", "failed", "abandoned"}),
    "running": frozenset({"completed", "failed", "abandoned"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "abandoned": frozenset(),
}


class InvalidTransitionError(Exception):
    """Attempted a status transition not in _ALLOWED_TRANSITIONS."""


class JobNotFoundError(Exception):
    """Operation targeted a job id that does not exist in the store."""


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    model_name: str
    input_json: str
    mode: JobMode
    status: JobStatus
    result_json: str | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    client_id: str | None
    prompt_id: str | None
    output_keys: list[str]
    response_delivered: bool
    initial_response_delivered: bool
    webhook_url: str | None
    webhook_headers: dict[str, str] | None
    webhook_delivery_status: WebhookDeliveryStatus | None
    webhook_handover: bool


_COLUMNS = (
    "id, model_name, input_json, mode, status, result_json, error_code, error_message, "
    "created_at, updated_at, client_id, prompt_id, output_keys, response_delivered, "
    "initial_response_delivered, webhook_url, webhook_headers_json, webhook_delivery_status, "
    "webhook_handover"
)


def _row_to_job(row: tuple) -> Job:
    (
        id_,
        model_name,
        input_json,
        mode,
        status,
        result_json,
        error_code,
        error_message,
        created_at,
        updated_at,
        client_id,
        prompt_id,
        output_keys_json,
        response_delivered,
        initial_response_delivered,
        webhook_url,
        webhook_headers_json,
        webhook_delivery_status,
        webhook_handover,
    ) = row
    return Job(
        id=id_,
        model_name=model_name,
        input_json=input_json,
        mode=mode,
        status=status,
        result_json=result_json,
        error_code=error_code,
        error_message=error_message,
        created_at=created_at,
        updated_at=updated_at,
        client_id=client_id,
        prompt_id=prompt_id,
        output_keys=json.loads(output_keys_json) if output_keys_json else [],
        response_delivered=bool(response_delivered),
        initial_response_delivered=bool(initial_response_delivered),
        webhook_url=webhook_url,
        webhook_headers=json.loads(webhook_headers_json) if webhook_headers_json else None,
        webhook_delivery_status=webhook_delivery_status,
        webhook_handover=bool(webhook_handover),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def create_queued(
    store: JobStore,
    *,
    model_name: str,
    input_json: str,
    mode: JobMode = "sync",
    webhook_url: str | None = None,
    webhook_headers: dict[str, str] | None = None,
) -> Job:
    job_id = f"gen_{Ksuid()}"
    now = _now()
    async with store.write() as conn:
        # Single round-trip via INSERT ... RETURNING (SQLite ≥ 3.35; Python 3.12's
        # bundled sqlite is 3.40+). Status is parameter-bound, not inline literal.
        cursor = await conn.execute(
            # _COLUMNS is a module constant (not user input); f-string is safe here.
            f"INSERT INTO jobs ({_COLUMNS}) VALUES "  # noqa: S608
            "(?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, NULL, NULL, NULL, 0, 0, ?, ?, NULL, 0) "
            f"RETURNING {_COLUMNS}",
            (
                job_id,
                model_name,
                input_json,
                mode,
                "queued",
                now,
                now,
                webhook_url,
                json.dumps(webhook_headers) if webhook_headers else None,
            ),
        )
        row = await cursor.fetchone()
    log.info("job.created", job_id=job_id, model=model_name, mode=mode)
    assert row is not None
    return _row_to_job(row)


async def get_by_id(store: JobStore, job_id: str) -> Job | None:
    conn = await store.read()
    cursor = await conn.execute(
        # _COLUMNS is a module constant (not user input); f-string is safe here.
        f"SELECT {_COLUMNS} FROM jobs WHERE id = ?",  # noqa: S608
        (job_id,),
    )
    row = await cursor.fetchone()
    return _row_to_job(row) if row else None


async def _fetch_status(conn, job_id: str) -> JobStatus:
    cursor = await conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
    row = await cursor.fetchone()
    if row is None:
        raise JobNotFoundError(job_id)
    return row[0]


def _assert_allowed(current: JobStatus, target: JobStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"{current} -> {target}")


async def set_running(store: JobStore, job_id: str, *, prompt_id: str, client_id: str) -> Job:
    async with store.write() as conn:
        current = await _fetch_status(conn, job_id)
        _assert_allowed(current, "running")
        await conn.execute(
            "UPDATE jobs SET status='running', prompt_id=?, client_id=?, updated_at=? WHERE id=?",
            (prompt_id, client_id, _now(), job_id),
        )
    log.info("job.running", job_id=job_id, prompt_id=prompt_id)
    fetched = await get_by_id(store, job_id)
    assert fetched is not None
    return fetched


async def set_completed(
    store: JobStore, job_id: str, *, output_keys: list[str], result_json: str
) -> Job:
    async with store.write() as conn:
        current = await _fetch_status(conn, job_id)
        _assert_allowed(current, "completed")
        await conn.execute(
            "UPDATE jobs SET status='completed', output_keys=?, result_json=?, "
            "updated_at=? WHERE id=?",
            (json.dumps(output_keys), result_json, _now(), job_id),
        )
    log.info("job.completed", job_id=job_id, output_count=len(output_keys))
    fetched = await get_by_id(store, job_id)
    assert fetched is not None
    return fetched


async def set_failed(store: JobStore, job_id: str, *, error_code: str, error_message: str) -> Job:
    async with store.write() as conn:
        current = await _fetch_status(conn, job_id)
        _assert_allowed(current, "failed")
        await conn.execute(
            "UPDATE jobs SET status='failed', error_code=?, error_message=?, "
            "updated_at=? WHERE id=?",
            (error_code, error_message, _now(), job_id),
        )
    log.info("job.failed", job_id=job_id, error_code=error_code)
    fetched = await get_by_id(store, job_id)
    assert fetched is not None
    return fetched
