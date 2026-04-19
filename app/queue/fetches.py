from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import aiosqlite
import structlog
from ksuid import Ksuid

from app.queue.store import JobStore

log = structlog.get_logger(__name__)

FetchStatus = Literal["pending", "downloading", "verifying", "done", "failed"]

_ALLOWED_TRANSITIONS: dict[FetchStatus, frozenset[FetchStatus]] = {
    "pending": frozenset({"downloading", "failed"}),
    "downloading": frozenset({"verifying", "failed"}),
    "verifying": frozenset({"done", "failed"}),
    "done": frozenset(),
    "failed": frozenset(),
}

_NON_TERMINAL: frozenset[FetchStatus] = frozenset({"pending", "downloading", "verifying"})


class InvalidTransitionError(Exception):
    """Attempted a status transition not in _ALLOWED_TRANSITIONS."""


class LoraFetchNotFoundError(Exception):
    """Operation targeted a fetch id that does not exist in the store."""


@dataclass(frozen=True, slots=True)
class LoraFetch:
    id: str
    url: str
    civitai_model_id: int | None
    civitai_version_id: int
    status: FetchStatus
    progress_bytes: int
    total_bytes: int | None
    dest_name: str | None
    error_code: str | None
    error_message: str | None
    handover: bool
    created_at: str
    updated_at: str


_COLUMNS = (
    "id, url, civitai_model_id, civitai_version_id, status, progress_bytes, "
    "total_bytes, dest_name, error_code, error_message, handover, "
    "created_at, updated_at"
)


def _row_to_fetch(row: tuple) -> LoraFetch:
    return LoraFetch(
        id=row[0],
        url=row[1],
        civitai_model_id=row[2],
        civitai_version_id=row[3],
        status=row[4],
        progress_bytes=row[5],
        total_bytes=row[6],
        dest_name=row[7],
        error_code=row[8],
        error_message=row[9],
        handover=bool(row[10]),
        created_at=row[11],
        updated_at=row[12],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def create_pending(
    store: JobStore,
    *,
    url: str,
    civitai_model_id: int | None,
    civitai_version_id: int,
) -> LoraFetch:
    """Insert a new `pending` row. Caller catches aiosqlite.IntegrityError
    from the unique-partial-index on active version_id and retries
    find_active_by_version to return the winner's id.
    """
    request_id = f"lfetch_{Ksuid()}"
    now = _now()
    async with store.write() as conn:
        cursor = await conn.execute(
            # _COLUMNS is a module constant; safe in f-string.
            f"INSERT INTO lora_fetches ({_COLUMNS}) VALUES "  # noqa: S608
            "(?, ?, ?, ?, ?, 0, NULL, NULL, NULL, NULL, 0, ?, ?) "
            f"RETURNING {_COLUMNS}",
            (
                request_id,
                url,
                civitai_model_id,
                civitai_version_id,
                "pending",
                now,
                now,
            ),
        )
        row = await cursor.fetchone()
    assert row is not None
    log.info(
        "lora_fetch.created",
        request_id=request_id,
        civitai_model_id=civitai_model_id,
        civitai_version_id=civitai_version_id,
    )
    return _row_to_fetch(row)


async def get_by_id(store: JobStore, request_id: str) -> LoraFetch | None:
    conn = await store.read()
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM lora_fetches WHERE id = ?",  # noqa: S608
        (request_id,),
    )
    row = await cursor.fetchone()
    return _row_to_fetch(row) if row else None


async def find_active_by_version(store: JobStore, civitai_version_id: int) -> LoraFetch | None:
    """Return the non-terminal row for this version, if any."""
    conn = await store.read()
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM lora_fetches "  # noqa: S608
        "WHERE civitai_version_id = ? "
        "AND status IN ('pending','downloading','verifying')",
        (civitai_version_id,),
    )
    row = await cursor.fetchone()
    return _row_to_fetch(row) if row else None


async def _fetch_status(conn: aiosqlite.Connection, request_id: str) -> FetchStatus:
    cursor = await conn.execute("SELECT status FROM lora_fetches WHERE id = ?", (request_id,))
    row = await cursor.fetchone()
    if row is None:
        raise LoraFetchNotFoundError(request_id)
    return row[0]


def _assert_allowed(current: FetchStatus, target: FetchStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"{current} -> {target}")


async def set_status(store: JobStore, request_id: str, target: FetchStatus) -> None:
    async with store.write() as conn:
        current = await _fetch_status(conn, request_id)
        _assert_allowed(current, target)
        await conn.execute(
            "UPDATE lora_fetches SET status = ?, updated_at = ? WHERE id = ?",
            (target, _now(), request_id),
        )
    log.info(
        "lora_fetch.status_changed",
        request_id=request_id,
        from_=current,
        to=target,
    )


async def set_total_bytes(store: JobStore, request_id: str, total: int) -> None:
    async with store.write() as conn:
        await conn.execute(
            "UPDATE lora_fetches SET total_bytes = ?, updated_at = ? WHERE id = ?",
            (total, _now(), request_id),
        )


async def set_progress(store: JobStore, request_id: str, progress: int) -> None:
    async with store.write() as conn:
        await conn.execute(
            "UPDATE lora_fetches SET progress_bytes = ?, updated_at = ? WHERE id = ?",
            (progress, _now(), request_id),
        )


async def set_dest_name(store: JobStore, request_id: str, dest_name: str) -> None:
    async with store.write() as conn:
        await conn.execute(
            "UPDATE lora_fetches SET dest_name = ?, updated_at = ? WHERE id = ?",
            (dest_name, _now(), request_id),
        )


async def set_failed(
    store: JobStore,
    request_id: str,
    *,
    error_code: str,
    error_message: str,
    handover: bool = False,
) -> None:
    async with store.write() as conn:
        current = await _fetch_status(conn, request_id)
        _assert_allowed(current, "failed")
        await conn.execute(
            "UPDATE lora_fetches SET status = 'failed', "
            "error_code = ?, error_message = ?, handover = ?, updated_at = ? "
            "WHERE id = ?",
            (error_code, error_message, 1 if handover else 0, _now(), request_id),
        )
    log.info(
        "lora_fetch.failed",
        request_id=request_id,
        error_code=error_code,
        error_message=error_message,
        handover=handover,
    )


async def scan_non_terminal(store: JobStore) -> list[LoraFetch]:
    """Return all rows currently in pending/downloading/verifying.
    Used by the boot-time recovery scan."""
    conn = await store.read()
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM lora_fetches "  # noqa: S608
        "WHERE status IN ('pending','downloading','verifying') "
        "ORDER BY updated_at",
    )
    rows = await cursor.fetchall()
    return [_row_to_fetch(r) for r in rows]
