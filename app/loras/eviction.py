"""LRU eviction for Civitai-fetched LoRAs.

Runs inside the fetcher's semaphore-gated task (one evictor at a time).
Operates on the `./loras/civitai/` subtree only — hand-dropped user LoRAs
(anywhere under `./loras/`, typically at top-level or sibling subdirs) are
never evicted.

Protection rules (all OR'd together — match any = protect):
  α) LoRA name referenced by `input_json` of any non-terminal job in `jobs`.
  β) LoRA sidecar has `last_used > now - recent_use_days`.
  γ) LoRA lacks a sidecar altogether (user hand-drop; conservative by design).

TOCTOU safeguard: after candidate selection, each unlink() re-queries α under
a fresh SQLite read to catch jobs enqueued in the window between selection
and delete.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from app.queue.store import JobStore

log = structlog.get_logger(__name__)


class InsufficientStorageError(Exception):
    """Even after maximum eviction, cannot fit incoming_size."""


def _directory_size(root: Path) -> int:
    """Sum of `.safetensors` file sizes under `root/civitai/**`. We don't count
    sidecars because they're negligible (~1 KiB each)."""
    civitai_root = root / "civitai"
    if not civitai_root.is_dir():
        return 0
    total = 0
    for path in civitai_root.rglob("*.safetensors"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


async def _non_terminal_lora_names(store: JobStore) -> set[str]:
    """Return the set of LoRA names referenced by any queued/running job's
    input_json. Used for α-protection."""
    conn = await store.read()
    cursor = await conn.execute("SELECT input_json FROM jobs WHERE status IN ('queued', 'running')")
    rows = await cursor.fetchall()
    names: set[str] = set()
    for (raw,) in rows:
        try:
            body = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        loras = body.get("loras") if isinstance(body, dict) else None
        if not isinstance(loras, list):
            continue
        for entry in loras:
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str):
                    names.add(name)
    return names


def _read_sidecar(sidecar_path: Path) -> dict | None:
    if not sidecar_path.is_file():
        return None
    try:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _last_used_dt(sidecar: dict | None) -> datetime | None:
    if sidecar is None:
        return None
    raw = sidecar.get("last_used")
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def evict_for(
    *,
    incoming_size: int,
    loras_root: Path,
    store: JobStore,
    dir_max_bytes: int,
    recent_use_days: int,
) -> int:
    """Delete stale civitai-fetched LoRAs to make room for `incoming_size`.

    Returns bytes reclaimed. Raises InsufficientStorageError if after evicting
    every eligible candidate we still can't fit incoming_size.
    """
    current_size = _directory_size(loras_root)
    required = incoming_size - (dir_max_bytes - current_size)
    if required <= 0:
        return 0

    civitai_root = loras_root / "civitai"
    if not civitai_root.is_dir():
        # Nothing under civitai/ to evict. User-drop only tree → refuse.
        raise InsufficientStorageError(
            f"need {required} bytes but ./loras/civitai/ is empty (nothing to evict)"
        )

    protected_by_jobs = await _non_terminal_lora_names(store)
    cutoff = datetime.now(UTC) - timedelta(days=recent_use_days)

    # Build candidate list of (last_used_dt, path, size, canonical_name)
    candidates: list[tuple[datetime, Path, int, str]] = []
    for path in civitai_root.rglob("*.safetensors"):
        if not path.is_file():
            continue
        relative = path.relative_to(loras_root)  # "civitai/slug_vid.safetensors"
        canonical_name = relative.with_suffix("").as_posix()
        # γ: no sidecar → skip (protect — could be an operator hand-drop)
        sidecar_path = path.with_suffix(".json")
        sidecar = _read_sidecar(sidecar_path)
        if sidecar is None:
            continue
        # γ-extended: sidecar exists but source is not "civitai" → skip. Prevents
        # accidental eviction of operator hand-drops that were annotated with a
        # sidecar (review-impl LOW-7). Only fetcher-written sidecars carry
        # source="civitai".
        if sidecar.get("source") != "civitai":
            continue
        # α: referenced by non-terminal job → skip
        if canonical_name in protected_by_jobs:
            continue
        # β: last_used within recent window → skip
        last_used = _last_used_dt(sidecar)
        sort_key = last_used or datetime.min.replace(tzinfo=UTC)
        if last_used is not None and last_used > cutoff:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        candidates.append((sort_key, path, size, canonical_name))

    # Oldest first (None-last_used → datetime.min sorts first, which is correct:
    # never-used entries evict before anything that has at least one use).
    candidates.sort(key=lambda row: row[0])

    reclaimed = 0
    for _sort_key, path, size, canonical_name in candidates:
        if reclaimed >= required:
            break
        # TOCTOU recheck: a job might have been enqueued between candidate
        # selection and now, referencing this exact LoRA. Re-query and skip
        # if so. Next candidate gets evicted instead.
        fresh_protected = await _non_terminal_lora_names(store)
        if canonical_name in fresh_protected:
            log.info(
                "lora.eviction.toctou_skip",
                name=canonical_name,
            )
            continue
        sidecar_path = path.with_suffix(".json")
        try:
            os.unlink(path)
            reclaimed += size
        except OSError as exc:
            log.warning(
                "lora.eviction.unlink_failed",
                path=str(path),
                error=str(exc),
            )
            continue
        try:
            os.unlink(sidecar_path)
        except OSError as exc:
            log.warning(
                "lora.eviction.sidecar_unlink_failed",
                sidecar=str(sidecar_path),
                error=str(exc),
            )
        log.info(
            "lora.eviction.deleted",
            name=canonical_name,
            size_bytes=size,
        )

    if reclaimed < required:
        raise InsufficientStorageError(
            f"need {required} bytes but only reclaimed {reclaimed} after "
            f"exhausting {len(candidates)} eligible candidates"
        )

    return reclaimed
