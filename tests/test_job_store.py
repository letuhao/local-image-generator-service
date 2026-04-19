from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.queue.jobs import (
    InvalidTransitionError,
    Job,
    JobNotFoundError,
    count_active,
    create_queued,
    get_by_id,
    mark_async_with_handover,
    mark_handover,
    mark_response_delivered,
    set_abandoned,
    set_completed,
    set_failed,
    set_fetched,
    set_running,
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


async def test_create_queued_roundtrip(store: JobStore) -> None:
    job = await create_queued(
        store,
        model_name="noobai-xl-vpred-1",
        input_json=json.dumps({"prompt": "hello"}),
    )
    assert job.id.startswith("gen_")
    assert job.model_name == "noobai-xl-vpred-1"
    assert job.mode == "sync"
    assert job.status == "queued"
    assert job.result_json is None
    assert job.error_code is None
    assert job.client_id is None
    assert job.prompt_id is None
    assert job.output_keys == []
    assert job.response_delivered is False
    assert job.initial_response_delivered is False
    assert job.webhook_url is None
    assert job.webhook_headers is None
    assert job.webhook_delivery_status is None
    assert job.webhook_handover is False

    fetched = await get_by_id(store, job.id)
    assert fetched == job


async def test_get_by_id_unknown_returns_none(store: JobStore) -> None:
    assert await get_by_id(store, "gen_nonexistent") is None


async def test_set_running_transitions_and_writes_adapter_ids(store: JobStore) -> None:
    job = await create_queued(store, model_name="m", input_json="{}")
    running = await set_running(store, job.id, prompt_id="pid-123", client_id="cid-abc")
    assert running.status == "running"
    assert running.prompt_id == "pid-123"
    assert running.client_id == "cid-abc"
    assert running.updated_at >= job.updated_at


async def test_set_completed_transitions_and_writes_outputs(store: JobStore) -> None:
    job = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, job.id, prompt_id="pid", client_id="cid")
    completed = await set_completed(
        store,
        job.id,
        output_keys=["bucket/key1.png", "bucket/key2.png"],
        result_json=json.dumps({"data": [{"url": "..."}]}),
    )
    assert completed.status == "completed"
    assert completed.output_keys == ["bucket/key1.png", "bucket/key2.png"]
    assert completed.result_json is not None
    assert json.loads(completed.result_json)["data"][0]["url"] == "..."


async def test_set_failed_writes_error(store: JobStore) -> None:
    job = await create_queued(store, model_name="m", input_json="{}")
    failed = await set_failed(store, job.id, error_code="comfy_timeout", error_message="boom")
    assert failed.status == "failed"
    assert failed.error_code == "comfy_timeout"
    assert failed.error_message == "boom"


async def test_illegal_transition_completed_to_running_raises(store: JobStore) -> None:
    job = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, job.id, prompt_id="pid", client_id="cid")
    await set_completed(store, job.id, output_keys=[], result_json="{}")
    with pytest.raises(InvalidTransitionError):
        await set_running(store, job.id, prompt_id="pid2", client_id="cid2")

    row = await get_by_id(store, job.id)
    assert row is not None
    assert row.status == "completed"


async def test_set_running_on_unknown_job_raises_not_found(store: JobStore) -> None:
    with pytest.raises(JobNotFoundError):
        await set_running(store, "gen_does_not_exist", prompt_id="pid", client_id="cid")


async def test_create_queued_concurrent_inserts_all_land_with_unique_ids(store: JobStore) -> None:
    async def one() -> Job:
        return await create_queued(store, model_name="m", input_json="{}")

    jobs = await asyncio.gather(*(one() for _ in range(50)))
    ids = {j.id for j in jobs}
    assert len(ids) == 50

    conn = await store.read()
    cursor = await conn.execute("SELECT COUNT(*) FROM jobs")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 50


async def test_concurrent_updates_serialise(store: JobStore) -> None:
    job = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, job.id, prompt_id="pid", client_id="cid")

    async def complete() -> Job:
        return await set_completed(store, job.id, output_keys=["a"], result_json="{}")

    async def fail() -> Job:
        try:
            return await set_failed(store, job.id, error_code="internal", error_message="x")
        except InvalidTransitionError:
            row = await get_by_id(store, job.id)
            assert row is not None
            return row

    results = await asyncio.gather(complete(), fail(), return_exceptions=True)
    terminal_statuses = {r.status for r in results if isinstance(r, Job)}
    assert terminal_statuses in ({"completed"}, {"completed", "failed"})

    row = await get_by_id(store, job.id)
    assert row is not None
    assert row.status in {"completed", "failed"}


async def test_wal_mode_enabled(store: JobStore) -> None:
    conn = await store.read()
    cursor = await conn.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0].lower() == "wal"


# ───────────────────────── migration runner edge cases ─────────────────────────


async def test_migration_runner_rejects_bad_filename(tmp_path: Path) -> None:
    """Files not matching ^\\d{3}_[a-z0-9_\\-]+\\.sql$ fail fast at startup."""
    import aiosqlite

    from app.queue.store import apply_migrations

    bad = tmp_path / "BADNAME.sql"
    bad.write_text("SELECT 1;")
    async with aiosqlite.connect(":memory:") as conn:
        with pytest.raises(RuntimeError, match=r"does not match NNN_<name>\.sql pattern"):
            await apply_migrations(conn, tmp_path)


async def test_migration_runner_rejects_duplicate_prefix(tmp_path: Path) -> None:
    """Two files with the same NNN_ prefix is a config error — fail fast."""
    import aiosqlite

    from app.queue.store import apply_migrations

    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "001_b.sql").write_text("SELECT 1;")
    async with aiosqlite.connect(":memory:") as conn:
        with pytest.raises(RuntimeError, match="numeric prefixes must be unique"):
            await apply_migrations(conn, tmp_path)


async def test_migration_runner_is_idempotent_on_reapply(tmp_path: Path) -> None:
    """Running apply_migrations a second time with no new files returns []."""
    import aiosqlite

    from app.queue.store import apply_migrations

    (tmp_path / "001_x.sql").write_text("CREATE TABLE t (id INTEGER);")
    async with aiosqlite.connect(str(tmp_path / "db.sqlite")) as conn:
        first = await apply_migrations(conn, tmp_path)
        assert first == ["001_x.sql"]

        second = await apply_migrations(conn, tmp_path)
        assert second == []

        # Simulate adding a new migration in a later cycle.
        (tmp_path / "002_y.sql").write_text("CREATE TABLE u (id INTEGER);")
        third = await apply_migrations(conn, tmp_path)
        assert third == ["002_y.sql"]

        fourth = await apply_migrations(conn, tmp_path)
        assert fourth == []


# ───────────────────────── Cycle 4 additions ─────────────────────────


async def test_fetched_at_column_exists(store: JobStore) -> None:
    """Migration 002_fetched_at.sql adds the column."""
    conn = await store.read()
    cursor = await conn.execute("PRAGMA table_info(jobs)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert "fetched_at" in cols


async def test_count_active_counts_queued_and_running(store: JobStore) -> None:
    assert await count_active(store) == 0
    j1 = await create_queued(store, model_name="m", input_json="{}")
    await create_queued(store, model_name="m", input_json="{}")
    assert await count_active(store) == 2
    await set_running(store, j1.id, prompt_id="pid", client_id="cid")
    assert await count_active(store) == 2  # running still counts
    await set_completed(store, j1.id, output_keys=[], result_json="{}")
    assert await count_active(store) == 1  # completed does not


async def test_set_fetched_is_idempotent_on_first_only(store: JobStore) -> None:
    """First call sets the timestamp; second call leaves the original in place."""
    job = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, job.id, prompt_id="pid", client_id="cid")
    await set_completed(store, job.id, output_keys=["a"], result_json="{}")

    await set_fetched(store, job.id)
    after_first = await get_by_id(store, job.id)
    assert after_first is not None
    assert after_first.fetched_at is not None
    first_stamp = after_first.fetched_at

    # Sleep briefly so now() would be different, then re-fetch.
    await asyncio.sleep(0.01)
    await set_fetched(store, job.id)
    after_second = await get_by_id(store, job.id)
    assert after_second is not None
    assert after_second.fetched_at == first_stamp  # unchanged


async def test_mark_response_delivered_sets_both_flags(store: JobStore) -> None:
    job = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, job.id, prompt_id="pid", client_id="cid")
    await set_completed(store, job.id, output_keys=["a"], result_json="{}")
    await mark_response_delivered(store, job.id)
    fetched = await get_by_id(store, job.id)
    assert fetched is not None
    assert fetched.response_delivered is True
    assert fetched.webhook_handover is True


async def test_mark_async_with_handover_flips_mode_keeps_response_delivered_false(
    store: JobStore,
) -> None:
    job = await create_queued(store, model_name="m", input_json="{}")
    assert job.mode == "sync"
    await mark_async_with_handover(store, job.id)
    fetched = await get_by_id(store, job.id)
    assert fetched is not None
    assert fetched.mode == "async"
    assert fetched.webhook_handover is True
    assert fetched.response_delivered is False


async def test_mark_handover_only_sets_handover_flag(store: JobStore) -> None:
    job = await create_queued(store, model_name="m", input_json="{}")
    await mark_handover(store, job.id)
    fetched = await get_by_id(store, job.id)
    assert fetched is not None
    assert fetched.webhook_handover is True
    assert fetched.response_delivered is False
    assert fetched.mode == "sync"  # unchanged


async def test_set_abandoned_transitions(store: JobStore) -> None:
    """queued or running → abandoned is allowed; terminal states can't."""
    job = await create_queued(store, model_name="m", input_json="{}")
    abandoned = await set_abandoned(store, job.id)
    assert abandoned.status == "abandoned"

    # From running
    j2 = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, j2.id, prompt_id="pid", client_id="cid")
    await set_abandoned(store, j2.id)
    row = await get_by_id(store, j2.id)
    assert row is not None
    assert row.status == "abandoned"

    # From completed should refuse.
    j3 = await create_queued(store, model_name="m", input_json="{}")
    await set_running(store, j3.id, prompt_id="pid", client_id="cid")
    await set_completed(store, j3.id, output_keys=[], result_json="{}")
    with pytest.raises(InvalidTransitionError):
        await set_abandoned(store, j3.id)
