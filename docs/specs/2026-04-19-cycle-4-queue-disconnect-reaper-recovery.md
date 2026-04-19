# Spec — Cycle 4: Queue worker + disconnect handler + orphan reaper + restart recovery

> **Cycle:** 4 of 11 · **Size:** L (7 core files, 5 logic areas, 1 side effect)
> **Parent plan:** [docs/plans/2026-04-18-image-gen-service-build.md §Cycle 4](../plans/2026-04-18-image-gen-service-build.md)
> **Arch refs:** §4.2 (queue + store + disconnect + recovery + reaper), §4.8 (webhook barrier rules), §6.1 (sync response), §12 (concurrency), §13 (error codes)
> **Author:** agent (letuhao1994 approved 2026-04-19)

---

## 1. Goal (verbatim from plan)

> Sync requests queue behind a single worker; disconnect mid-request doesn't orphan a blob; process restart mid-job produces terminal status for the client (not a 404).

Done means:
- `POST /v1/images/generations` **enqueues** a job instead of calling the adapter directly. Depth-gated at `MAX_QUEUE` (arch §12) by SQLite ground-truth count.
- Single worker `asyncio.Task` serializes GPU work (one adapter call in flight at a time).
- Client disconnect mid-request: worker still finishes, job state flips to `mode=async, webhook_handover=true`, S3 objects persist. Sync response (if any) goes to `/dev/null` but the DB is the source of truth.
- Process restart: boot-scan sees `queued`→re-enqueue, `running`→`failed{service_restarted}, webhook_handover=true`. No orphan SQLite rows. Cycle 8/9's poll + dispatcher read terminal state.
- Orphan reaper: deletes S3 objects of `completed` jobs that were never fetched (`fetched_at IS NULL AND updated_at < now - ORPHAN_REAPER_TTL`).

## 2. Decisions locked in CLARIFY

| Q | Decision |
|---|---|
| Q1 Never-fetched signal | **`fetched_at` column** (migration `002_fetched_at.sql`). Gateway updates it on any 2xx GET (includes HEAD). Reaper key: `status='completed' AND fetched_at IS NULL AND updated_at < cutoff`. |
| Q2 MAX_QUEUE guard | **SQLite count gate BEFORE `create_queued`** — `SELECT COUNT(*) WHERE status IN ('queued','running') >= MAX_QUEUE → 429`. `asyncio.Queue(maxsize=MAX_QUEUE)` as the in-memory signaling mechanism; recovery uses blocking `put` (no data-loss if asyncio queue is full mid-recovery). |
| Q3 `asyncio.shield` scope | Wraps **only** the `await job_completion_future`. Worker's full submit→wait→fetch→upload chain lives in its own task, untouched by shield. |
| Q4 `is_disconnected()` cadence | **Side-task polls every 500 ms.** Spawned per request, cancelled on completion or on first detected disconnect. On disconnect: commit `mode='async', webhook_handover=true` to SQLite. |
| Q5 `response_delivered` flush hook | **FastAPI `BackgroundTasks`.** Scheduled after handler return; runs after response bytes are produced. Commits `response_delivered=true, webhook_handover=true` together. Documented at-least-once crash-race inherits from arch §4.2 (no new liability). |

## 3. In scope (this cycle only)

- `migrations/002_fetched_at.sql` — `ALTER TABLE jobs ADD COLUMN fetched_at TEXT` + supporting index `idx_jobs_completed_unfetched`.
- `app/queue/__init__.py` already exists; adding:
  - `app/queue/worker.py` — `QueueWorker` class with `run()` loop, `enqueue(job, future)`, `shutdown()`.
  - `app/queue/reaper.py` — `OrphanReaper` class, background loop.
  - `app/queue/recovery.py` — `recover_jobs(store, adapter_client_id)` called once at lifespan start.
- `app/queue/jobs.py` updates — add `set_abandoned`, `set_fetched`, `mark_async_with_handover`, `mark_response_delivered` CRUD helpers. Pyramid of helpers over the single store-write primitive; no schema logic leaks into handlers.
- `app/api/images.py` rewrites — POST now enqueues + awaits the future under `asyncio.shield` + spawns a disconnect watcher + uses `BackgroundTasks` to flush `response_delivered`. GET updates `fetched_at` on success.
- `app/main.py` lifespan — runs `recover_jobs` before spawning the worker; spawns `QueueWorker.run()` + `OrphanReaper.run()` as app-state tasks; cancels them on shutdown.
- `.env.example` — already has `MAX_QUEUE=20`, `ORPHAN_REAPER_TTL=86400`, `JOB_RECORD_TTL=604800`. Confirm.
- Tests:
  - `tests/test_queue_worker.py` — 3 jobs queued, serial execution verified; MAX_QUEUE triggers 429; worker survives adapter errors.
  - `tests/test_disconnect.py` — simulate `is_disconnected() → True` mid-wait; assert worker still completes, job flips to `mode=async, webhook_handover=true`, S3 object exists.
  - `tests/test_restart_recovery.py` — write `running` + `queued` rows into SQLite before boot, assert lifespan transitions `running → failed{service_restarted}, webhook_handover=true` and `queued` re-enqueues.
  - `tests/test_orphan_reaper.py` — write `completed` + `fetched_at=NULL` + old `updated_at`; run one reap cycle; assert S3 object deleted + `fetched_at` still null (reaper doesn't touch) + `status` still completed.
  - Integration piggyback: `tests/integration/test_e2e_sync.py` now indirectly exercises the queue (no separate integration test for the queue itself — unit fakes cover the state machine).

## 4. Out of scope (explicit descope)

- **No async-mode endpoint yet** — Cycle 8. `mode=async` still rejected by Pydantic with `async_not_enabled`. But the mode-flip on disconnect goes into the DB regardless; Cycle 8's poll endpoint will read it.
- **No webhook dispatcher** — Cycle 9. The `webhook_handover=true` column is set; no code consumes it yet.
- **No queue prioritization, fairness, or SLO classes** — single FIFO queue.
- **No multi-worker / horizontal scaling** — `WORKERS=N` is a Cycle 10+ consideration.
- **No cancellation endpoint** (`DELETE /v1/images/generations/{id}`) — deferred.
- **No SQLite row pruning** (`JOB_RECORD_TTL`) — the reaper deletes S3 objects only; jobs rows stay forever this cycle. Pruning is Cycle 10 hygiene.
- **No adapter call retry wrapping at the worker layer.** Worker calls adapter exactly once. Adapter's internal WS-reconnect + poll-fallback is the only retry. Future cycle may add a worker-level retry with different error-class handling.

## 5. File plan (final list)

| # | Path | Kind | Notes |
|---|---|---|---|
| 1 | `migrations/002_fetched_at.sql` | new | ADD COLUMN + index |
| 2 | `app/queue/worker.py` | new | QueueWorker class |
| 3 | `app/queue/reaper.py` | new | OrphanReaper class |
| 4 | `app/queue/recovery.py` | new | `recover_jobs(store)` |
| 5 | `app/queue/jobs.py` | modify | +`set_abandoned`, +`set_fetched`, +`mark_response_delivered`, +`mark_async_with_handover`, +`count_active` |
| 6 | `app/api/images.py` | modify | POST enqueues; GET updates fetched_at; BackgroundTask |
| 7 | `app/main.py` | modify | lifespan runs recovery; spawns worker + reaper |
| 8 | `tests/test_queue_worker.py` | new | 4-5 tests |
| 9 | `tests/test_disconnect.py` | new | 3 tests |
| 10 | `tests/test_restart_recovery.py` | new | 3 tests |
| 11 | `tests/test_orphan_reaper.py` | new | 3 tests |
| 12 | `tests/conftest.py` | modify | swap adapter + s3 at fixture level; pre-populate SQLite for recovery test |
| 13 | `tests/test_image_get.py` | modify | assert `fetched_at` gets set on GET |

## 6. Test matrix

### tests/test_queue_worker.py
- `enqueue + worker.run` processes a single job: job transitions `queued → running → completed`, future resolves with `JobResult`.
- 3 jobs enqueued concurrently → worker processes serially (order via timestamps).
- `count_active(store) >= MAX_QUEUE` → handler returns 429 with `error_code=queue_full`, no row created.
- Worker handles `ComfyNodeError` from adapter: job → `failed{comfy_error}`, future rejects, worker continues next job.
- Worker shutdown cancels the task cleanly (asserted via `task.cancelled()`).

### tests/test_disconnect.py
- `is_disconnected() → True` after 100 ms of a 2 s wait → job ends with `mode='async', webhook_handover=true`, status=completed (worker still ran), S3 object exists.
- Disconnect during `queued` (before worker picks up) → flip mode, worker still processes.
- No disconnect → normal completion, `mode='sync'`, `response_delivered=true`, `webhook_handover=true` (both flags set by the BackgroundTask).

### tests/test_restart_recovery.py
- Seed SQLite with `running` row; boot lifespan; assert row is now `failed, error_code='service_restarted', webhook_handover=true`.
- Seed with `queued` row + asyncio queue empty on boot; assert worker picks it up (eventually `completed` or terminal state).
- Seed with `completed` row → recovery leaves it untouched.

### tests/test_orphan_reaper.py
- Seed `completed` job with `fetched_at=NULL` and `updated_at` in the past → reaper deletes S3 object, row unchanged except the S3 key is gone.
- Seed same but `fetched_at=<now>` → reaper skips it.
- Seed `running` job with old `updated_at` → reaper skips (only targets completed).

### tests/test_image_get.py (updated)
- Add assertion: after a successful GET, `job.fetched_at` is set in the DB (query via test's store fixture).
- Two successive GETs don't rewrite `fetched_at` (once-only; optional — could rewrite, behavior-neutral for reaper).

## 7. Data flow (sync request, Cycle 4)

```
POST /v1/images/generations  Bearer …
  │
  ▼
app.api.images.create_image:
  1. Pydantic validate → GenerateRequest
  2. resolve_and_validate → ValidatedJob (model, steps, …)
  3. count_active(store)                            ─── SQLite
     if >= MAX_QUEUE → 429 queue_full (no row created)
  4. create_queued(store, …) → Job (status=queued)
  5. fut = await worker.enqueue(job)                ─── asyncio.Queue.put
  6. disconnect_watcher = asyncio.create_task(watch(…))
  7. try:
        result = await asyncio.shield(fut)          ─── blocks until worker signals
     except CancelledError:
        # handler's own task cancelled by Starlette — worker runs on
        raise                                          # (response will be discarded)
     finally:
        disconnect_watcher.cancel()
  8. background_tasks.add_task(mark_response_delivered(store, job.id))
  9. return JSONResponse + X-Job-Id header

WORKER (QueueWorker.run in lifespan-managed task):
  while True:
    job, future = await asyncio.Queue.get()
    try:
      # prepare graph (same as Cycle 3 create_image lines 4-5)
      prompt_id = await adapter.submit(graph)
      await set_running(store, job.id, prompt_id=…, client_id=adapter.client_id)
      await adapter.wait_for_completion(prompt_id, timeout_s=job_timeout_s)
      images = await adapter.fetch_outputs(prompt_id)
      # validate + upload (same as Cycle 3)
      for idx, png in enumerate(images):
          _raise_if_not_png(png)
          s3.upload_png(job.id, idx, png)
      await set_completed(store, job.id, output_keys=…, result_json=…)
      future.set_result(JobResult(data=…))
    except BackendError as exc:
      await set_failed(store, job.id, error_code=exc.error_code, error_message=str(exc))
      future.set_exception(exc)
    except Exception as exc:
      await set_failed(store, job.id, error_code="internal", error_message=str(exc))
      future.set_exception(exc)

DISCONNECT WATCHER (per-request asyncio.Task):
  while True:
    if await request.is_disconnected():
      await mark_async_with_handover(store, job.id)
      return
    await asyncio.sleep(0.5)

BOOT RECOVERY (lifespan, before worker starts):
  scan jobs WHERE status IN ('queued', 'running')
  for each:
    if status == 'running':
      await set_failed(store, j.id, error_code='service_restarted',
                       error_message='process restart mid-generation')
      await mark_handover(store, j.id)          # webhook_handover=true
    elif status == 'queued':
      await queue.put(Job(…))                   # blocking put; capacity OK fresh

ORPHAN REAPER (lifespan-managed task):
  while True:
    await asyncio.sleep(600)                    # 10 min cadence
    cutoff = now - ORPHAN_REAPER_TTL
    rows = SELECT id, output_keys FROM jobs
           WHERE status='completed'
             AND fetched_at IS NULL
             AND updated_at < cutoff
    for each:
      for each s3_key in output_keys:
        try: s3.delete_object(bucket, key)
        except StorageError: log, continue
      # job row not touched — keeps audit trail
```

## 8. Design — concrete API contracts

### 8.1 `migrations/002_fetched_at.sql`

```sql
-- Cycle 4: track when a completed job's image(s) were first fetched via the
-- gateway. Reaper targets completed-but-never-fetched rows.
ALTER TABLE jobs ADD COLUMN fetched_at TEXT;

-- Composite index supports reaper scan: WHERE status='completed' AND
-- fetched_at IS NULL AND updated_at < cutoff.
CREATE INDEX IF NOT EXISTS idx_jobs_completed_unfetched
    ON jobs(status, fetched_at, updated_at);
```

### 8.2 `app/queue/jobs.py` additions

```python
async def count_active(store: JobStore) -> int:
    """Return count of jobs in 'queued' or 'running' state. Used for MAX_QUEUE gate."""

async def set_abandoned(
    store: JobStore, job_id: str, *, error_code: str = "service_stopping"
) -> Job: ...

async def set_fetched(store: JobStore, job_id: str) -> None:
    """Record that the image was fetched via the GET gateway. Idempotent-noop if
    already set (we keep the first-fetch timestamp)."""

async def mark_response_delivered(store: JobStore, job_id: str) -> None:
    """Set response_delivered=1 AND webhook_handover=1 atomically. Called after
    BackgroundTask runs. If the job was already flipped to async by the disconnect
    watcher, this is a no-op on response_delivered (keep it false) and no-op on
    webhook_handover (already true)."""

async def mark_async_with_handover(store: JobStore, job_id: str) -> None:
    """Flip mode='async' + webhook_handover=1 atomically. Called by disconnect
    watcher. Does NOT touch response_delivered (stays false)."""

async def mark_handover(store: JobStore, job_id: str) -> None:
    """Set webhook_handover=1 without touching mode or response_delivered.
    Used by boot recovery for `running` rows flipped to `failed`."""

class Job:  # dataclass, existing — add field
    fetched_at: str | None        # ISO-8601 timestamp when GET served the image
```

Update `_row_to_job` to populate `fetched_at`. Update `_COLUMNS` to include it.

### 8.3 `app/queue/worker.py`

```python
@dataclass
class JobResult:
    data: list[dict[str, Any]]   # response `data[]` entries
    duration_ms: float
    resolved_seed: int | None    # only populated when seed=-1 was resolved

class QueueWorker:
    def __init__(
        self,
        *,
        store: JobStore,
        adapter: BackendAdapter,
        s3: S3Storage,
        registry: Registry,
        public_base_url: str,
        job_timeout_s: float,
        max_queue: int,
    ) -> None: ...

    async def enqueue(self, job: Job) -> asyncio.Future[JobResult] | None:
        """Put (job, future) on the queue. Returns the future for a normal request
        (the handler awaits it). For recovery (`future_needed=False` internally),
        returns None — the recovered job has no handler waiting."""

    async def run(self) -> None:
        """Main loop. For each dequeued item: re-parse + re-resolve validation from
        job.input_json (single code path; recovery doesn't need a separate branch),
        prepare the workflow graph, call adapter, upload to S3, resolve the future.
        Catches BackendError + Exception. Never raises from the loop itself.
        Returns when cancelled."""

    async def enqueue_recovery(self, job: Job) -> None:
        """Boot-only: awaits put without allocating a future (no handler to wait)."""
```

**Single-path design:** the worker's `run()` loop re-validates from `job.input_json` on every iteration. This eliminates the handler→worker data-class hand-off and makes recovery reuse the exact same code path. Slight CPU duplication (resolve_and_validate runs twice per request — once for 400 response, once inside worker) — negligible in practice.

**Consequence:** if registry changes between `create_queued` and worker dequeue (e.g., via future `/admin/reload`), the worker uses the current registry. A model removal mid-flight would cause `failed{validation_error}` — acceptable, arch §4.4 lock around registry reload prevents mid-job mutation anyway.

### 8.4 `app/queue/reaper.py`

```python
class OrphanReaper:
    def __init__(
        self,
        *,
        store: JobStore,
        s3: S3Storage,
        ttl_seconds: int,
        scan_interval_seconds: int = 600,  # 10-min cadence
    ) -> None: ...

    async def run(self) -> None:
        """Scan every `scan_interval_seconds`. Identify candidate rows; delete S3
        objects for each; log counts. Cancelled on shutdown."""

    async def reap_once(self) -> int:
        """One scan pass. Returns count of S3 objects deleted. Exposed for tests."""
```

### 8.5 `app/queue/recovery.py`

```python
async def recover_jobs(store: JobStore, worker: QueueWorker) -> dict[str, int]:
    """One-shot boot scan. Returns a stats dict: {'requeued': N, 'failed_restart': M}.

    Transitions:
      - status='running' → failed{service_restarted}, webhook_handover=true.
      - status='queued'  → re-enqueue via worker.enqueue_recovery(job).
    """
```

### 8.6 `app/api/images.py` rewrite

```python
@router.post("/v1/images/generations")
async def create_image(
    request: Request,
    background_tasks: BackgroundTasks,
    kid: str = Depends(require_auth),
) -> JSONResponse:
    # 1. Parse + validate (Pydantic + resolve_and_validate).
    # 2. count_active(store) >= MAX_QUEUE → 429 queue_full (no row).
    # 3. create_queued.
    # 4. enqueue via worker.enqueue(job, validated).
    # 5. disconnect_watcher = asyncio.create_task(_watch_disconnect(request, store, job.id)).
    # 6. try:
    #        result = await asyncio.shield(fut)
    #    finally:
    #        disconnect_watcher.cancel()
    # 7. background_tasks.add_task(mark_response_delivered, store, job.id).
    # 8. Build + return response (url or b64, matching resolved_format).


async def _watch_disconnect(request, store, job_id, interval=0.5):
    while True:
        if await request.is_disconnected():
            await mark_async_with_handover(store, job_id)
            return
        await asyncio.sleep(interval)


@router.api_route("/v1/images/{job_id}/{index_name}", methods=["GET", "HEAD"])
async def get_image(...):
    # Existing logic. After successful S3 fetch, call set_fetched(store, job_id).
    # Idempotent — first 2xx wins on the timestamp.
```

### 8.7 `app/main.py` lifespan extensions

```
startup (additions to Cycle 3):
  1-5. (unchanged) configure_logging, store.connect, registry, s3, adapter
  6. worker = QueueWorker(store, adapter, s3, registry, public_base_url, job_timeout_s, max_queue)
  7. reaper = OrphanReaper(store, s3, ttl_seconds=ORPHAN_REAPER_TTL)
  8. app.state.worker = worker; app.state.reaper = reaper
  9. recovery_stats = await recover_jobs(store, worker)
     log("lifespan.recovery_done", **recovery_stats)
  10. app.state.worker_task = asyncio.create_task(worker.run(), name="queue-worker")
  11. app.state.reaper_task = asyncio.create_task(reaper.run(), name="orphan-reaper")

shutdown (reverse):
  - cancel reaper_task, await it
  - cancel worker_task, await it (worker handles pending with set_abandoned)
  - await adapter.close()
  - await store.close()
```

### 8.8 Error envelope (new cases)

| Condition | Status | `error.code` |
|---|---|---|
| `count_active(store) >= MAX_QUEUE` | 429 | `queue_full` |
| Worker raises BackendError (as before) | mapped by error_code | unchanged |
| Recovery marks running→failed | n/a (internal) | persisted as `service_restarted` |

### 8.9 Env vars (already in `.env.example`)

- `MAX_QUEUE=20` — queue depth gate.
- `ORPHAN_REAPER_TTL=86400` — 24 h.
- `JOB_TIMEOUT_S=300` — unchanged from Cycle 3.
- `ORPHAN_REAPER_SCAN_INTERVAL_S=600` — new; add to `.env.example`.

## 9. Risks + mitigations

| Risk | Mitigation |
|---|---|
| `asyncio.shield` doesn't save worker from cancellation when event loop shuts down hard | Worker is a top-level task spawned by lifespan; shield protects the handler's `await`, not the worker. Handler cancelled on shutdown → response lost (acceptable, dispatcher will fire in Cycle 9). |
| Disconnect watcher fires spuriously on keep-alive drops | `is_disconnected()` returns True only on TCP FIN / reset per Starlette. Keep-alive timeouts manifest as disconnect, which is the correct semantic here. |
| BackgroundTask runs AFTER uvicorn writes the response but crash between → `response_delivered=false` persists → dispatcher fires when it shouldn't | Documented at-least-once (arch §4.2 sync flush race); receiver must dedupe. |
| `count_active` race: two concurrent handlers both see N < MAX_QUEUE and both create | asyncio single-threaded + `await count_active()` is atomic from the caller's POV; worst case one request exceeds the limit by 1 under extreme contention. Document as negligible. |
| Worker exception escapes the loop and kills the task | Broad `except Exception` inside the loop catches everything; loop continues. Only `asyncio.CancelledError` (from shutdown) exits. |
| Reaper deletes an S3 object mid-fetch (edge: client fetches right at TTL boundary) | GET returns 404 `not_found` — caller retries, the DB row still says completed but bytes gone. Acceptable; documented. Also mitigated by 10-min reaper cadence + 24-h default TTL (wide margin). |
| Recovery re-enqueue floods the worker on boot | Normal case: recovery <= MAX_QUEUE (SQLite gate enforced these rows originally). `worker.enqueue_recovery` uses blocking `await put` so asyncio queue honors capacity; the startup log line counts restored rows. |
| Two workers somehow spawn (Cycle 10 `WORKERS=N`) | Out of scope. When it lands, asyncio.Queue + SQLite `FOR UPDATE` semantics need re-design. |
| `fetched_at` updates on HEAD + GET both → test assertion about "once-only" needs care | Default `set_fetched` is conditional INSERT-if-null via `UPDATE ... WHERE fetched_at IS NULL`. Idempotent by design. |
| Orphan reaper deletes S3 bytes but row says `output_keys=[…]` still | Acceptable: row is an audit record. Any future fetch gets 404. If we care, Cycle 10 TTL prune will null out `output_keys` alongside the S3 delete. |
| `asyncio.Queue(maxsize=MAX_QUEUE)` fills mid-recovery, `enqueue_recovery`'s `await put` deadlocks | Deadlock requires worker NOT consuming — worker_task is spawned AFTER recovery completes per lifespan order. Fix: spawn worker BEFORE recovery OR don't gate recovery's `put` at the in-memory layer (use `put_nowait` + asyncio.Queue(maxsize=0) during recovery). **Chose:** spawn worker first, then recovery — worker drains while recovery is still pushing. Update lifespan §8.7 order. |

## 10. Self-review checklist

- [x] No placeholders, no TBDs
- [x] Every file in §5 has a purpose + test coverage in §6
- [x] CLARIFY Q1–Q5 answers locked in §2 and reflected in §8 contracts
- [x] Descope scan: no Cycle 5+ leakage (LoRA injection, Civitai fetch, Chroma, async-mode endpoint, webhook dispatcher, rate limiting — all excluded)
- [x] Error envelope updated with `queue_full`
- [x] Lifespan order revised (§9 risk table) so recovery doesn't deadlock on the queue
- [x] Migration is additive — no destructive schema change

---

*End of spec.*
