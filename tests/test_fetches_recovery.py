from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.queue.fetches import (
    create_pending,
    get_by_id,
    set_status,
)
from app.queue.fetches_recovery import recover_fetches
from app.queue.store import JobStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[JobStore]:
    s = JobStore(str(tmp_path / "jobs.db"))
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


async def test_non_terminal_rows_flipped_to_failed(store: JobStore, tmp_path: Path) -> None:
    loras_root = tmp_path / "loras"
    loras_root.mkdir()

    pending = await create_pending(store, url="a", civitai_model_id=1, civitai_version_id=100)
    downloading = await create_pending(store, url="b", civitai_model_id=2, civitai_version_id=101)
    await set_status(store, downloading.id, "downloading")

    terminal = await create_pending(store, url="c", civitai_model_id=3, civitai_version_id=102)
    await set_status(store, terminal.id, "downloading")
    await set_status(store, terminal.id, "verifying")
    await set_status(store, terminal.id, "done")

    stats = await recover_fetches(store, loras_root)

    assert stats.rows_handed_over == 2
    pending_after = await get_by_id(store, pending.id)
    downloading_after = await get_by_id(store, downloading.id)
    terminal_after = await get_by_id(store, terminal.id)
    assert pending_after.status == "failed"
    assert pending_after.error_code == "service_restarted"
    assert pending_after.handover is True
    assert downloading_after.status == "failed"
    assert downloading_after.handover is True
    assert terminal_after.status == "done"  # untouched


async def test_tmp_files_cleaned_up(store: JobStore, tmp_path: Path) -> None:
    loras_root = tmp_path / "loras"
    civitai = loras_root / "civitai"
    civitai.mkdir(parents=True)
    (civitai / "a.safetensors.tmp").write_bytes(b"partial")
    (civitai / "b.safetensors").write_bytes(b"complete")  # not a .tmp
    sub = civitai / "sub"
    sub.mkdir()
    (sub / "c.safetensors.tmp").write_bytes(b"partial")

    stats = await recover_fetches(store, loras_root)

    assert stats.tmp_files_cleaned == 2
    assert not (civitai / "a.safetensors.tmp").exists()
    assert (civitai / "b.safetensors").exists()  # untouched
    assert not (sub / "c.safetensors.tmp").exists()


async def test_recover_on_empty_state_is_noop(store: JobStore, tmp_path: Path) -> None:
    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    stats = await recover_fetches(store, loras_root)
    assert stats.rows_handed_over == 0
    assert stats.tmp_files_cleaned == 0
