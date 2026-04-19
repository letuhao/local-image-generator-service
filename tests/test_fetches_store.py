from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from app.queue.fetches import (
    InvalidTransitionError,
    LoraFetchNotFoundError,
    create_pending,
    find_active_by_version,
    get_by_id,
    scan_non_terminal,
    set_dest_name,
    set_failed,
    set_progress,
    set_status,
    set_total_bytes,
)
from app.queue.store import JobStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[JobStore]:
    s = JobStore(str(tmp_path / "jobs.db"))
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


async def test_create_pending_round_trips(store: JobStore) -> None:
    lf = await create_pending(
        store,
        url="https://civitai.com/models/1?modelVersionId=2",
        civitai_model_id=1,
        civitai_version_id=2,
    )
    assert lf.status == "pending"
    assert lf.progress_bytes == 0
    assert lf.total_bytes is None
    assert lf.dest_name is None
    assert lf.handover is False
    got = await get_by_id(store, lf.id)
    assert got == lf


async def test_valid_status_progression(store: JobStore) -> None:
    lf = await create_pending(
        store,
        url="x",
        civitai_model_id=None,
        civitai_version_id=10,
    )
    await set_status(store, lf.id, "downloading")
    await set_progress(store, lf.id, 1024)
    await set_total_bytes(store, lf.id, 4096)
    await set_status(store, lf.id, "verifying")
    await set_dest_name(store, lf.id, "civitai/foo_10")
    await set_status(store, lf.id, "done")
    got = await get_by_id(store, lf.id)
    assert got is not None
    assert got.status == "done"
    assert got.progress_bytes == 1024
    assert got.total_bytes == 4096
    assert got.dest_name == "civitai/foo_10"


async def test_invalid_transition_rejected(store: JobStore) -> None:
    lf = await create_pending(
        store,
        url="x",
        civitai_model_id=None,
        civitai_version_id=20,
    )
    await set_status(store, lf.id, "downloading")
    await set_status(store, lf.id, "verifying")
    await set_status(store, lf.id, "done")
    with pytest.raises(InvalidTransitionError):
        await set_status(store, lf.id, "pending")


async def test_set_failed_records_error(store: JobStore) -> None:
    lf = await create_pending(
        store,
        url="x",
        civitai_model_id=None,
        civitai_version_id=30,
    )
    await set_failed(store, lf.id, error_code="sha_mismatch", error_message="hash differs")
    got = await get_by_id(store, lf.id)
    assert got is not None
    assert got.status == "failed"
    assert got.error_code == "sha_mismatch"
    assert got.error_message == "hash differs"
    assert got.handover is False


async def test_set_failed_handover_flag(store: JobStore) -> None:
    lf = await create_pending(
        store,
        url="x",
        civitai_model_id=None,
        civitai_version_id=31,
    )
    await set_failed(
        store,
        lf.id,
        error_code="service_restarted",
        error_message="boot",
        handover=True,
    )
    got = await get_by_id(store, lf.id)
    assert got is not None
    assert got.handover is True


async def test_set_status_unknown_id_raises(store: JobStore) -> None:
    with pytest.raises(LoraFetchNotFoundError):
        await set_status(store, "lfetch_nonexistent", "downloading")


async def test_find_active_by_version_returns_non_terminal_only(
    store: JobStore,
) -> None:
    # Active — should be found.
    active = await create_pending(
        store,
        url="a",
        civitai_model_id=1,
        civitai_version_id=100,
    )
    await set_status(store, active.id, "downloading")

    # Terminal — should be ignored.
    done = await create_pending(
        store,
        url="b",
        civitai_model_id=2,
        civitai_version_id=200,
    )
    await set_status(store, done.id, "downloading")
    await set_status(store, done.id, "verifying")
    await set_status(store, done.id, "done")

    assert (await find_active_by_version(store, 100)).id == active.id
    assert await find_active_by_version(store, 200) is None
    assert await find_active_by_version(store, 999) is None


async def test_unique_partial_index_blocks_duplicate_active(
    store: JobStore,
) -> None:
    await create_pending(
        store,
        url="a",
        civitai_model_id=1,
        civitai_version_id=42,
    )
    # Second INSERT for same version_id while first is still active → IntegrityError.
    with pytest.raises(aiosqlite.IntegrityError):
        await create_pending(
            store,
            url="b",
            civitai_model_id=1,
            civitai_version_id=42,
        )


async def test_scan_non_terminal_returns_in_flight(store: JobStore) -> None:
    a = await create_pending(
        store,
        url="a",
        civitai_model_id=1,
        civitai_version_id=300,
    )
    b = await create_pending(
        store,
        url="b",
        civitai_model_id=2,
        civitai_version_id=301,
    )
    await set_status(store, b.id, "downloading")

    c = await create_pending(
        store,
        url="c",
        civitai_model_id=3,
        civitai_version_id=302,
    )
    await set_status(store, c.id, "downloading")
    await set_status(store, c.id, "verifying")
    await set_status(store, c.id, "done")

    rows = await scan_non_terminal(store)
    assert {r.id for r in rows} == {a.id, b.id}
