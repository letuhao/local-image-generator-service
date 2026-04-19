# Cycle 4 — task plan

> **Spec:** [docs/specs/2026-04-19-cycle-4-queue-disconnect-reaper-recovery.md](../specs/2026-04-19-cycle-4-queue-disconnect-reaper-recovery.md)
> **Size:** L · **Execution mode:** Inline, sequential.
> **Commit strategy:** Single end-of-cycle commit.

---

## Chunk A — Migration + jobs.py extensions (TDD)

### A1. Red: migration-runner + jobs.py CRUD tests
**Files:** `tests/test_job_store.py` (modify)
**Intent:** Add tests for `count_active`, `set_fetched`, `mark_response_delivered`, `mark_async_with_handover`, `mark_handover`, `set_abandoned`. Also assert `migration 002` gets applied + `fetched_at` column exists after `JobStore.connect()`.
**Verify:** fails with AttributeError on new helpers.

### A2. Green: migration + jobs.py
**Files:** `migrations/002_fetched_at.sql` (new), `app/queue/jobs.py` (modify)
**Intent:**
- Migration: `ALTER TABLE jobs ADD COLUMN fetched_at TEXT` + `CREATE INDEX idx_jobs_completed_unfetched`.
- `Job` dataclass: add `fetched_at: str | None`. Update `_COLUMNS` + `_row_to_job`.
- New async helpers per spec §8.2: `count_active`, `set_fetched` (conditional update `WHERE fetched_at IS NULL`), `mark_response_delivered` (sets both `response_delivered=1` + `webhook_handover=1`), `mark_async_with_handover` (flips `mode='async'` + `webhook_handover=1`), `mark_handover` (just `webhook_handover=1`), `set_abandoned` (status→abandoned).
**Verify:** `uv run pytest tests/test_job_store.py -q` → all prior + new green.

---

## Chunk B — Worker (TDD)

### B1. Red: worker tests
**Files:** `tests/test_queue_worker.py` (new)
**Intent:** Per spec §6. 5 tests:
- `enqueue` puts job + returns future; worker run consumes it; future resolves with `JobResult`; DB status progresses `queued→running→completed`.
- 3 concurrent enqueues → 3 futures resolve in sequence (worker serializes).
- Worker handles `ComfyNodeError`: future rejects with the error; loop continues to next job.
- Worker survives generic `Exception` in adapter: `failed{internal}`, future rejects.
- `task.cancel()` followed by await → clean exit (no stray work).

Tests use `_FakeAdapter` (copy pattern from Cycle 3's sync endpoint tests) + `_FakeS3` (in-memory dict).
**Verify:** fails with ImportError on `app.queue.worker`.

### B2. Green: QueueWorker
**Files:** `app/queue/worker.py` (new)
**Intent:** Per spec §8.3. `QueueWorker` class; `enqueue(job) → future | None`; `run()` loop that:
1. Dequeues `(job, future | None)`.
2. Re-parses `job.input_json` → Pydantic → `resolve_and_validate` → `ValidatedJob`. On failure: `failed{validation_error}`, future rejects (if present).
3. Builds graph (copy of Cycle 3 handler lines 82-116: anchor lookup + seed random + latent dims).
4. `adapter.submit` + `set_running` + `wait_for_completion` + `fetch_outputs` + PNG check + upload.
5. `set_completed` + future.set_result(`JobResult(data, duration_ms, resolved_seed)`).
6. Exception handling mirrors Cycle 3 (comfy_unreachable/comfy_error/comfy_timeout/storage_error/internal).

**Helper extraction:** move Cycle 3's graph-prep + adapter chain from `app/api/images.py` into `app/queue/worker.py`. Handler loses ~80 lines.
**Verify:** worker tests pass.

---

## Chunk C — Recovery (TDD)

### C1. Red: recovery tests
**Files:** `tests/test_restart_recovery.py` (new)
**Intent:** Per spec §6:
- Pre-seed `running` row; boot lifespan (via `LifespanManager`); assert row is `failed{service_restarted}, webhook_handover=true`.
- Pre-seed `queued` row; boot; assert worker picks it up (eventually reaches `completed` via mocked adapter) — fixture hooks wait on its terminal state.
- Pre-seed `completed` row; boot; assert untouched.
**Verify:** fails — no `app.queue.recovery` module.

### C2. Green: recover_jobs
**Files:** `app/queue/recovery.py` (new)
**Intent:** Per spec §8.5. `async def recover_jobs(store, worker) -> dict[str, int]`. Scans via `store.read()`; transitions; returns `{'requeued': N, 'failed_restart': M}`. Uses `set_failed` + `mark_handover` for running; `worker.enqueue_recovery(job)` for queued.
**Verify:** recovery tests pass.

---

## Chunk D — Reaper (TDD)

### D1. Red: reaper tests
**Files:** `tests/test_orphan_reaper.py` (new)
**Intent:** Per spec §6. 3 tests: deletes S3 object when `status=completed + fetched_at=NULL + updated_at<cutoff`; skips when `fetched_at` set; skips `running` jobs. Uses `_FakeS3` that tracks `delete_object` calls.
**Verify:** fails — no `app.queue.reaper`.

### D2. Green: OrphanReaper
**Files:** `app/queue/reaper.py` (new)
**Intent:** Per spec §8.4. `OrphanReaper` class with `run()` (periodic loop) + `reap_once()` (exposed for tests).
**Verify:** reaper tests pass.

---

## Chunk E — Handler rewrite + disconnect watcher (TDD)

### E1. Red: disconnect tests
**Files:** `tests/test_disconnect.py` (new)
**Intent:** Per spec §6. 3 tests. Mock `request.is_disconnected` to return True after a controlled delay. Use `FakeAdapter` that takes a configurable pause before returning the PNG so disconnection can happen mid-wait.
**Verify:** fails — current handler doesn't have a disconnect watcher.

### E2. Green: images.py rewrite
**Files:** `app/api/images.py` (modify)
**Intent:** Per spec §8.6. POST handler:
1. Pydantic + resolve_and_validate (for 400).
2. `count_active(store)` gate → 429.
3. `create_queued`.
4. `fut = await worker.enqueue(job)`.
5. `watcher = asyncio.create_task(_watch_disconnect(request, store, job.id))`.
6. `try: result = await asyncio.shield(fut); finally: watcher.cancel()`.
7. `background_tasks.add_task(mark_response_delivered, store, job.id)`.
8. Build + return response.

Plus: GET handler calls `set_fetched(store, job_id)` after successful S3 fetch (before returning the Response).
**Verify:** disconnect tests pass; all pre-existing Cycle 3 tests still pass (handler behavior unchanged for normal paths).

---

## Chunk F — Main wiring + integration

### F1. Lifespan + conftest updates
**Files:** `app/main.py` (modify), `tests/conftest.py` (modify)
**Intent:** Per spec §8.7:
- Lifespan spawns `worker_task` + `reaper_task` BEFORE `recover_jobs` (fixes the deadlock risk identified in spec §9 #9).
- Scan lifespan order: store.connect → registry → s3 → adapter → **worker_task + reaper_task spawned** → recover_jobs → yield.
- Shutdown: cancel reaper_task → cancel worker_task → await both → adapter.close → store.close.
- Conftest: add `QueueWorker` + `OrphanReaper` stubs (or accept the real lifespan wiring with mocked adapter). Ensure `client` fixture works with the new lifespan without leaking tasks.
**Verify:** full suite green including existing tests (no regressions from the handler rewrite).

### F2. Full suite + lint + smoke
**Verify:**
- `uv run pytest -q -m "not integration"` green.
- `uv run ruff check .` clean.
- `uv run ruff format --check .` clean.
- `docker compose build image-gen-service && docker compose up -d --force-recreate image-gen-service` → healthy.
- Live smoke:
  ```
  curl -s -X POST -H "Authorization: Bearer test-gen-key" \
    -H "Content-Type: application/json" \
    -d '{"model":"noobai-xl-v1.1","prompt":"queue test","size":"512x512","steps":1}' \
    http://127.0.0.1:8700/v1/images/generations | jq .
  ```
- Integration test re-run: `uv run pytest -m integration -q tests/integration/test_e2e_sync.py` — should still pass through the queue.

---

## Order of execution

```
A1 → A2                      # migration + jobs.py CRUD (TDD)
 ↓
B1 → B2                      # worker (TDD); requires A done
 ↓
C1 → C2                      # recovery (TDD); requires B done
 ↓
D1 → D2                      # reaper (TDD); independent of B/C but after A
 ↓
E1 → E2                      # handler rewrite + disconnect (TDD); requires B
 ↓
F1 → F2                      # lifespan wiring + verify
```

## Commit message template

```
feat(cycle-4): queue worker + disconnect handler + orphan reaper + restart recovery

- migrations/002_fetched_at.sql: ADD COLUMN fetched_at + index.
- app/queue/worker.py: single asyncio.Task serializes GPU work; re-resolves
  validation from job.input_json on dequeue (single path for fresh + recovered).
- app/queue/reaper.py: periodic scan deletes S3 objects of completed jobs
  never fetched within ORPHAN_REAPER_TTL.
- app/queue/recovery.py: boot scan — `queued` → re-enqueue,
  `running` → `failed{service_restarted}, webhook_handover=true`.
- app/queue/jobs.py: +count_active, +set_fetched, +mark_response_delivered,
  +mark_async_with_handover, +mark_handover, +set_abandoned.
- app/api/images.py: POST enqueues + awaits under asyncio.shield; per-request
  disconnect watcher flips mode=async on drop; BackgroundTasks flushes
  response_delivered after response returns.
- app/main.py: lifespan spawns worker + reaper tasks BEFORE recover_jobs
  (recovery deadlock fix).
- Tests: test_queue_worker (5), test_disconnect (3), test_restart_recovery
  (3), test_orphan_reaper (3), test_image_get (updated: fetched_at).
```

## Risks during BUILD

| Risk | Mitigation during build |
|---|---|
| Existing Cycle 3 sync tests break when handler delegates to worker | worker tests use the same `_FakeAdapter` + `_FakeS3` fakes; handler tests swap them via app.state. Pattern already proven in `test_sync_endpoint.py`. |
| `asyncio.shield(fut)` interacts weirdly with Starlette cancellation | Test disconnect using a controllable mock of `Request.is_disconnected`; assert worker still completes regardless. |
| Recovery test needs seeded SQLite pre-lifespan | Use a conftest fixture that opens `aiosqlite` directly, runs migrations, seeds rows, closes, THEN lets lifespan re-open. |
| Worker task's background log lines leak into unrelated tests via structlog contextvars | Worker binds `job_id` / `prompt_id` via contextvars; should `clear_contextvars()` between items to avoid bleed. Test this explicitly. |
| BackgroundTasks in tests: do they actually run under `LifespanManager` + `ASGITransport`? | Yes — Starlette runs background_tasks after response via the same event loop. Test asserts `response_delivered=true` post-response (small sleep in test). |
| `count_active` race (two handlers see N<MAX simultaneously) | Accept — asyncio single-threaded; at most +1 overshoot. Documented. |
| Recovery floods worker on boot | `enqueue_recovery` uses `await put` (blocks on capacity). Worker consumes concurrently. Lifespan blocks until `recover_jobs` returns (i.e., all recovered items on the queue). |
| Runtime-deps trap (fresh memory) — any new import? | None new: asyncio, httpx, boto3, structlog, pydantic all already runtime. No new deps expected. |
| Docker image needs rebuild for migration 002 to be present | `COPY migrations/` already in Dockerfile; new SQL file picked up by next build. Remember to rebuild before F2 smoke. |

---

*End of task plan.*
