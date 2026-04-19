"""Boot-time recovery for lora_fetches.

Any row in pending|downloading|verifying at process start is a leftover from
a crashed previous run. We flip each to `failed{service_restarted, handover=true}`
so the poll endpoint surfaces a clean terminal state to the caller. Partial
`.safetensors.tmp` files under `./loras/civitai/` get unlinked.

Spec decision Q4: handover, no resume. Caller retries the original fetch URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from app.queue.fetches import scan_non_terminal, set_failed
from app.queue.store import JobStore

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FetchRecoveryStats:
    rows_handed_over: int
    tmp_files_cleaned: int


async def recover_fetches(store: JobStore, loras_root: Path) -> FetchRecoveryStats:
    """Flip non-terminal rows → failed{service_restarted}, clean stale tmp files."""
    rows = await scan_non_terminal(store)
    for row in rows:
        await set_failed(
            store,
            row.id,
            error_code="service_restarted",
            error_message="fetcher interrupted by service restart",
            handover=True,
        )

    tmp_cleaned = 0
    civitai_root = loras_root / "civitai"
    if civitai_root.is_dir():
        for tmp in civitai_root.rglob("*.safetensors.tmp"):
            try:
                tmp.unlink()
                tmp_cleaned += 1
            except OSError as exc:
                log.warning(
                    "fetches_recovery.tmp_unlink_failed",
                    path=str(tmp),
                    error=str(exc),
                )

    stats = FetchRecoveryStats(rows_handed_over=len(rows), tmp_files_cleaned=tmp_cleaned)
    log.info(
        "fetches_recovery.done",
        rows_handed_over=stats.rows_handed_over,
        tmp_files_cleaned=stats.tmp_files_cleaned,
    )
    return stats
