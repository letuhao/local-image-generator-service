from __future__ import annotations

import structlog

from app.queue.jobs import (
    mark_handover,
    scan_non_terminal,
    set_failed,
)
from app.queue.store import JobStore
from app.queue.worker import QueueWorker

log = structlog.get_logger(__name__)


async def recover_jobs(store: JobStore, worker: QueueWorker) -> dict[str, int]:
    """Boot scan (arch §4.2).

    Scans for non-terminal rows left over from a prior process lifetime:
      - status='running' → transition to failed{service_restarted, webhook_handover=true}.
      - status='queued'  → re-enqueue on the in-memory queue (worker task MUST
                           already be consuming to avoid deadlock).

    Returns a stats dict so the lifespan can log recovery counts at INFO.
    """
    jobs = await scan_non_terminal(store)

    requeued = 0
    failed_restart = 0
    for job in jobs:
        if job.status == "running":
            await set_failed(
                store,
                job.id,
                error_code="service_restarted",
                error_message="process restart mid-generation",
            )
            await mark_handover(store, job.id)
            failed_restart += 1
            log.info("recovery.running_flipped", job_id=job.id)
        elif job.status == "queued":
            # Blocking put; requires worker consuming concurrently (lifespan spawns
            # worker task BEFORE calling recover_jobs).
            await worker.enqueue_recovery(job)
            requeued += 1
            log.info("recovery.queued_requeued", job_id=job.id)

    log.info("recovery.done", requeued=requeued, failed_restart=failed_restart)
    return {"requeued": requeued, "failed_restart": failed_restart}
