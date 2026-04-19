from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from app.queue.store import JobStore

log = structlog.get_logger(__name__)


class OrphanReaper:
    """Periodically deletes S3 objects of completed-but-never-fetched jobs.

    Arch §4.2: "a background task deletes S3 objects belonging to jobs that
    reached `completed` but whose result was never fetched within ORPHAN_REAPER_TTL."

    Keys on `fetched_at IS NULL` (migration 002) + `updated_at < now - ttl`.
    Does NOT touch the jobs row — keeps the audit trail; future cycles may add
    row pruning via `JOB_RECORD_TTL`.
    """

    def __init__(
        self,
        *,
        store: JobStore,
        s3: Any,  # duck-typed: S3Storage or test fake
        ttl_seconds: int,
        scan_interval_seconds: int = 600,
    ) -> None:
        self._store = store
        self._s3 = s3
        self._ttl = ttl_seconds
        self._interval = scan_interval_seconds

    async def run(self) -> None:
        """Main loop — one pass every `scan_interval_seconds`. Cancel to exit."""
        log.info("orphan_reaper.started", ttl_s=self._ttl, interval_s=self._interval)
        try:
            while True:
                try:
                    deleted = await self.reap_once()
                    if deleted:
                        log.info("orphan_reaper.pass", deleted=deleted)
                except Exception as exc:  # pragma: no cover — defensive
                    log.exception("orphan_reaper.error", error=str(exc))
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            log.info("orphan_reaper.cancelled")
            raise

    async def reap_once(self) -> int:
        """One scan pass. Returns count of S3 objects successfully deleted."""
        cutoff = (datetime.now(UTC) - timedelta(seconds=self._ttl)).isoformat()
        conn = await self._store.read()
        cursor = await conn.execute(
            "SELECT id, output_keys FROM jobs WHERE status='completed' "
            "AND fetched_at IS NULL AND updated_at < ?",
            (cutoff,),
        )
        rows = await cursor.fetchall()

        deleted = 0
        for row in rows:
            _job_id, output_keys_json = row
            if not output_keys_json:
                continue
            import json as _json

            keys = _json.loads(output_keys_json)
            for entry in keys:
                bucket, _, key = entry.partition("/")
                try:
                    await self._s3.delete_object(bucket, key)
                    deleted += 1
                except Exception as exc:  # pragma: no cover — defensive
                    log.warning(
                        "orphan_reaper.delete_failed",
                        bucket=bucket,
                        key=key,
                        error=str(exc),
                    )
        return deleted
