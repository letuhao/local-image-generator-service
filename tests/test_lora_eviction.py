from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.loras.eviction import InsufficientStorageError, evict_for
from app.queue.jobs import create_queued
from app.queue.store import JobStore


def _write_lora(
    root: Path,
    name: str,
    *,
    size: int,
    last_used: datetime | None = None,
    sidecar: bool = True,
) -> None:
    """Drop a LoRA under root/<name>.safetensors + sibling .json."""
    path = root / f"{name}.safetensors"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    if sidecar:
        sidecar_path = path.with_suffix(".json")
        data: dict[str, object] = {"source": "civitai"}
        if last_used is not None:
            data["last_used"] = last_used.isoformat()
        sidecar_path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[JobStore]:
    s = JobStore(str(tmp_path / "jobs.db"))
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


async def test_no_eviction_needed_returns_zero(store: JobStore, tmp_path: Path) -> None:
    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    reclaimed = await evict_for(
        incoming_size=100,
        loras_root=loras_root,
        store=store,
        dir_max_bytes=1_000_000,
        recent_use_days=7,
    )
    assert reclaimed == 0


async def test_stale_lora_evicted(store: JobStore, tmp_path: Path) -> None:
    loras_root = tmp_path / "loras"
    stale = datetime.now(UTC) - timedelta(days=30)
    _write_lora(loras_root / "civitai", "old_42", size=500, last_used=stale)

    reclaimed = await evict_for(
        incoming_size=400,
        loras_root=loras_root,
        store=store,
        dir_max_bytes=600,  # current=500, need 400 → required = 400-(600-500)=300
        recent_use_days=7,
    )
    assert reclaimed == 500
    assert not (loras_root / "civitai" / "old_42.safetensors").exists()
    assert not (loras_root / "civitai" / "old_42.json").exists()


async def test_recent_lora_protected(store: JobStore, tmp_path: Path) -> None:
    loras_root = tmp_path / "loras"
    recent = datetime.now(UTC) - timedelta(hours=2)
    _write_lora(loras_root / "civitai", "fresh_100", size=500, last_used=recent)

    with pytest.raises(InsufficientStorageError):
        await evict_for(
            incoming_size=400,
            loras_root=loras_root,
            store=store,
            dir_max_bytes=600,
            recent_use_days=7,
        )
    # fresh file survives
    assert (loras_root / "civitai" / "fresh_100.safetensors").exists()


async def test_active_job_protects_stale_lora(store: JobStore, tmp_path: Path) -> None:
    loras_root = tmp_path / "loras"
    stale = datetime.now(UTC) - timedelta(days=30)
    _write_lora(loras_root / "civitai", "in_use_7", size=500, last_used=stale)

    # Queue a job referencing this lora.
    body = {"model": "x", "prompt": "y", "loras": [{"name": "civitai/in_use_7", "weight": 0.5}]}
    await create_queued(store, model_name="x", input_json=json.dumps(body), mode="sync")

    with pytest.raises(InsufficientStorageError):
        await evict_for(
            incoming_size=400,
            loras_root=loras_root,
            store=store,
            dir_max_bytes=600,
            recent_use_days=7,
        )
    assert (loras_root / "civitai" / "in_use_7.safetensors").exists()


async def test_user_drop_without_sidecar_protected(store: JobStore, tmp_path: Path) -> None:
    loras_root = tmp_path / "loras"
    _write_lora(loras_root / "civitai", "stray", size=500, sidecar=False)

    with pytest.raises(InsufficientStorageError):
        await evict_for(
            incoming_size=400,
            loras_root=loras_root,
            store=store,
            dir_max_bytes=600,
            recent_use_days=7,
        )
    assert (loras_root / "civitai" / "stray.safetensors").exists()


async def test_user_drop_with_non_civitai_source_sidecar_protected(
    store: JobStore, tmp_path: Path
) -> None:
    """/review-impl LOW-7: a sidecar with `source != "civitai"` is a hand-dropped
    operator file; eviction must not touch it even if it's stale and under
    civitai/."""
    loras_root = tmp_path / "loras"
    civitai = loras_root / "civitai"
    civitai.mkdir(parents=True)
    stale = datetime.now(UTC) - timedelta(days=30)
    path = civitai / "user_drop.safetensors"
    path.write_bytes(b"\x00" * 500)
    path.with_suffix(".json").write_text(
        json.dumps({"source": "local", "last_used": stale.isoformat()}),
        encoding="utf-8",
    )

    with pytest.raises(InsufficientStorageError):
        await evict_for(
            incoming_size=400,
            loras_root=loras_root,
            store=store,
            dir_max_bytes=600,
            recent_use_days=7,
        )
    assert path.exists()
    assert path.with_suffix(".json").exists()


async def test_insufficient_storage_raises(store: JobStore, tmp_path: Path) -> None:
    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    # Empty civitai subdir means no candidates.
    (loras_root / "civitai").mkdir()
    with pytest.raises(InsufficientStorageError):
        await evict_for(
            incoming_size=1_000_000,
            loras_root=loras_root,
            store=store,
            dir_max_bytes=100,
            recent_use_days=7,
        )


async def test_eviction_removes_sidecar_too(store: JobStore, tmp_path: Path) -> None:
    loras_root = tmp_path / "loras"
    stale = datetime.now(UTC) - timedelta(days=30)
    _write_lora(loras_root / "civitai", "evictable", size=500, last_used=stale)

    await evict_for(
        incoming_size=400,
        loras_root=loras_root,
        store=store,
        dir_max_bytes=600,
        recent_use_days=7,
    )
    assert not (loras_root / "civitai" / "evictable.safetensors").exists()
    assert not (loras_root / "civitai" / "evictable.json").exists()


async def test_toctou_recheck_skips_newly_referenced_candidate(
    store: JobStore, tmp_path: Path
) -> None:
    """Simulate the race: candidate is selected, then a job references it
    before unlink, recheck catches it, we fall through to the next candidate."""
    loras_root = tmp_path / "loras"
    stale = datetime.now(UTC) - timedelta(days=30)
    _write_lora(loras_root / "civitai", "candidate_a", size=500, last_used=stale)
    # second candidate also stale, slightly newer
    slightly_less_stale = datetime.now(UTC) - timedelta(days=15)
    _write_lora(
        loras_root / "civitai",
        "candidate_b",
        size=500,
        last_used=slightly_less_stale,
    )

    # Patch _non_terminal_lora_names so the FIRST call (during candidate
    # collection) returns empty, and the SECOND call (TOCTOU recheck in the
    # delete loop) returns {candidate_a} — simulating a job arriving mid-loop.
    calls = {"n": 0}
    real_names = {"civitai/candidate_a"}

    import app.loras.eviction as ev

    async def fake(store):
        calls["n"] += 1
        return set() if calls["n"] == 1 else real_names

    from unittest.mock import patch

    with patch.object(ev, "_non_terminal_lora_names", fake):
        # current=1000 bytes (two 500-byte files); dir_max=1000; incoming=500
        # → required = 500 - (1000-1000) = 500 bytes. One eviction of 500 fits.
        reclaimed = await evict_for(
            incoming_size=500,
            loras_root=loras_root,
            store=store,
            dir_max_bytes=1000,
            recent_use_days=7,
        )

    # candidate_a should have survived (TOCTOU skip), candidate_b got evicted
    assert reclaimed == 500
    assert (loras_root / "civitai" / "candidate_a.safetensors").exists()
    assert not (loras_root / "civitai" / "candidate_b.safetensors").exists()
