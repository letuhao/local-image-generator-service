# Session log

> Append the newest sprint at the top. Keep each entry short: one-line outcome, changed files, notable decisions, what's next.

**Last session ended:** 2026-04-19 after Sprint 7 / Cycle 4 complete. Resume from [HANDOFF.md](HANDOFF.md) — it holds the pick-up-where-you-left-off summary.

---

## Sprint 7 — 2026-04-19 — Cycle 4 queue worker + disconnect handler + orphan reaper + restart recovery complete

**Outcome:** `POST /v1/images/generations` now routes through an `asyncio.Queue`-bounded single worker. Client disconnect mid-request flips `mode=async, webhook_handover=true` (dispatcher-ready for Cycle 9). Process restart recovers left-over rows cleanly. Orphan S3 objects get reaped via `fetched_at IS NULL` scan every 10 min. 180 tests green (177 unit + 3 integration), ruff + format clean. Live end-to-end through the queue confirmed.

**Files created / modified (14):**

- **Schema:** `migrations/002_fetched_at.sql` — `ADD COLUMN fetched_at TEXT` + `idx_jobs_completed_unfetched` composite index.
- **Worker:** `app/queue/worker.py` — `QueueWorker` class. Re-validates `job.input_json` on every dequeue so fresh requests and boot-recovered jobs share one pipeline. Timeout path calls `_safe_cancel` + `_safe_free` to release VRAM (arch §12). `set_running` failure cancels the ComfyUI prompt to avoid untracked runs.
- **Reaper:** `app/queue/reaper.py` — periodic scan keyed on `status='completed' AND fetched_at IS NULL AND updated_at < cutoff`; deletes S3 objects, leaves job rows alone (Cycle 10 owns `JOB_RECORD_TTL`).
- **Recovery:** `app/queue/recovery.py` — boot scan. `running` → `failed{service_restarted}, webhook_handover=true`. `queued` → `enqueue_recovery` (blocking put; worker MUST already be consuming — lifespan order enforces it).
- **Jobs helpers** (`app/queue/jobs.py`): +`count_active`, `scan_non_terminal`, `set_fetched` (idempotent via `WHERE fetched_at IS NULL`), `mark_response_delivered` (sets both flags, guarded by `mode='sync'`), `mark_async_with_handover`, `mark_handover`, `set_abandoned`. `Job` dataclass gains `fetched_at`.
- **Handler:** `app/api/images.py` — POST now enqueues, awaits under `asyncio.shield` (client disconnect cancelling the handler doesn't cancel the worker), spawns a per-request disconnect watcher polling `is_disconnected()` every 500ms, uses `BackgroundTasks` to flush `response_delivered` after the response writes. GET updates `fetched_at` via `set_fetched`.
- **Lifespan** (`app/main.py`): spawns `worker_task` + `reaper_task` BEFORE `recover_jobs` (recovery deadlock fix — recovery's `await queue.put` needs a consumer). Threads `async_mode_enabled` into `QueueWorker`. Hard-cancel on shutdown (graceful-drain deferred to Cycle 10, documented inline).
- **S3 storage:** `app/storage/s3.py` — `+delete_object` (idempotent on NoSuchKey) for the reaper.
- **Tests:** `test_queue_worker.py` (5), `test_disconnect.py` (2), `test_restart_recovery.py` (3), `test_orphan_reaper.py` (4). `test_sync_endpoint.py` updated with a post-response `response_delivered` flush assertion. `test_image_get.py` updated with pre/post `fetched_at` assertion. `test_job_store.py` gains 7 tests for the new jobs.py helpers.
- **Arch §4.2** gains a "Seed non-determinism on recovery" paragraph — seed=-1 is re-randomized on a recovery-triggered re-run; callers needing reproducibility across restarts MUST pass an explicit seed.

**Decisions locked in CLARIFY:**

- `fetched_at` column (not `response_delivered`) keys the reaper — gateway-GET is the first-fetch signal.
- SQLite `count_active` before `create_queued` — no DB rows for rejected requests.
- `asyncio.shield` wraps ONLY the future await; worker task runs independently.
- Disconnect watcher polls every 500 ms.
- `BackgroundTasks` for post-response `response_delivered=true` flush.

**Bugs caught during BUILD:**

- **Cycle 3 sync-endpoint tests broke after the handler rewrite.** They swapped `app.state.adapter` but the handler now delegates to `app.state.worker._adapter`. Fixture updated to patch both.
- **`comfy_error` vs `internal` classification.** Zero-output + non-PNG now map to `comfy_error` (ComfyUI's output problem, not unclassified 5xx). DB + handler + tests aligned.
- **Disconnect test initially raced.** Watcher polls at 500 ms; worker was finishing in 301 ms. Made the fake adapter slower (1.2 s) + forced `is_disconnected=True` on first call.

**`/review-impl` pass found 10 findings, all fixed in the same cycle:**

- **MED-1** Worker now calls `_safe_cancel` + `_safe_free` on `ComfyTimeoutError` (arch §12). Previously ComfyUI would keep running the prompt with VRAM stuck.
- **MED-2** `set_running` wrapped; on SQLite write failure, we cancel the submitted ComfyUI prompt. Prevents "untracked running prompt" if a recovery-triggered rerun would otherwise duplicate generation.
- **MED-3** Shutdown is hard-cancel only; comment in lifespan flags the `SHUTDOWN_GRACE_S=90` as Cycle 10 deferred.
- **LOW-4** `async_mode_enabled` threaded into `QueueWorker.__init__` + read in re-validation. Future-proofs against flag flips during ongoing ops.
- **LOW-5** Arch §4.2 documents seed=-1 non-determinism across restarts.
- **LOW-6** Accepted — `mark_async_with_handover`'s unconditional UPDATE is correct per arch §4.8 decision table.
- **LOW-7** `test_get_image_returns_png_bytes` now asserts `fetched_at` is NULL pre-fetch and set post-fetch.
- **LOW-8** New `test_sync_generation_sets_response_delivered_and_handover` — poll-and-assert on the BackgroundTask's DB commit.
- **COSMETIC-9** Deferred per arch (Cycle 10 JOB_RECORD_TTL owns row prune).
- **COSMETIC-10** Added `scan_non_terminal` public helper in `app/queue/jobs.py`; `app/queue/recovery.py` no longer imports `_COLUMNS` / `_row_to_job`.

**Live verification:**

```
pytest (full)     180/180 pass (177 unit + 3 integration)
ruff              clean
ruff format       clean
docker compose    image-gen-service rebuilt; lifespan log:
                     registry.loaded → queue_worker.started →
                     orphan_reaper.started → recovery.done{req=0,fail=0} →
                     service.started
Live POST         routes through queue; worker picks up; 200 + gateway URL
Live GET          streams from S3; fetched_at set; reaper on next scan skips it
```

### Retro — lessons worth keeping

- **`asyncio.shield(fut)` scope matters more than it looks.** Wrapping JUST the future awaits lets the handler take client-disconnect cancellation while the worker task (running independently) continues. Wrapping too much (e.g. the whole adapter call) would delay shutdown; wrapping too little would cancel the worker mid-job. The worker-in-its-own-task design makes the shield unnecessary for the worker itself; shield only protects the handler's view.
- **Lifespan task ordering matters when recovery is blocking.** `recover_jobs` → `enqueue_recovery` → `await queue.put()` — and that await blocks on capacity UNLESS a worker is draining. My initial intuition was "run recovery before starting worker task" (feels safer); that deadlocks if MAX_QUEUE recovered rows come up. Spawning worker first + relying on its concurrent consumption is correct. Captured in spec §9 risk #9 + lifespan comment.
- **BackgroundTasks are "after response" but not "after flush".** Starlette runs them after the handler returns the Response object, which is before uvicorn writes the last byte. A crash between "response returned" and "bytes hit wire" leaves `response_delivered=false` committed, dispatcher fires the webhook, client never sees the response. That's at-least-once by design (arch §4.2); don't try to make it exactly-once — the dispatcher contract already mandates durable dedupe at the receiver.
- **`Literal` annotations still lie at runtime, part 2.** Cycle 3's lesson came back: the `mark_async_with_handover` helper doesn't guard on `mode IN ('sync',)` — it unconditionally flips. That's deliberate (race with completion) but means typed annotations ≠ runtime invariants. Whenever a mutation crosses a state boundary, decide and document whether the mutation is idempotent or has preconditions.
- **The "worker runs independently" model simplifies error semantics dramatically.** Every worker error → DB write → future.set_exception. Handler just catches the future exception. No shared state between handler's cancellation path and worker's processing path. Clean.

**What's next (Sprint 8 plan / Cycle 5):**

1. `app/loras/scanner.py` — walk `./loras/`, read sidecars (`<name>.json`), return `LoraMeta` list.
2. `app/api/loras.py` — `GET /v1/loras` (any scope).
3. `app/registry/workflows.py` gets two new functions: `inject_loras(graph, loras)` implementing arch §9 chain algorithm, `inject_vpred(graph)` (deferred body but scaffolded).
4. `app/validation.py` stops rejecting `loras`; adds the §6.0 bounds + path-realpath containment check.
5. `workflows/sdxl_eps.json` stays anchor-tagged as-is; injection runs at request time.
6. Tests: `test_lora_scanner.py`, `test_graph_injection.py`, `test_path_traversal.py`. Integration test with a real LoRA showing visible-effect difference.

**Prerequisites for Cycle 5:**
- At least one compatible SDXL LoRA placed in `./loras/` with a `<name>.json` sidecar (user to provide or fetch from Civitai manually pre-cycle).

**Commits this sprint:** 1 feat + 1 docs session-close.

---

## Sprint 6 — 2026-04-19 — Cycle 3 MinIO gateway + registry + first sync POST endpoint complete

**Outcome:** `POST /v1/images/generations` now accepts a prompt + model name and returns a JSON body pointing at our own `GET /v1/images/{job_id}/{index}.png` gateway. The GET streams PNG bytes back from internal MinIO via Bearer auth. First cycle where LoreWeave can actually call the service end-to-end. 158 tests green (155 unit + 3 integration), ruff + format clean. Arch v0.6 landed covering the Q4 gateway-vs-presign redirect.

**Files created / modified (26):**

- **Storage:** `app/storage/s3.py` (single boto3 client, tenacity retry on transient codes only, idempotent `ensure_bucket`, `object_key_for` pure helper).
- **Registry:** `app/registry/models.py` + `config/models.yaml` — `Registry` + `load_registry` with 9-stage validation (checkpoint/vae/workflow existence + anchors + VRAM + sampler/scheduler enums + duplicate-name + prediction/backend enums + empty-registry).
- **Validation:** `app/validation.py` — Pydantic `GenerateRequest` with `extra=forbid` + post-Pydantic `resolve_and_validate` merging model defaults + enforcing per-model limits. Rejects webhook/lora/mode=async/unknown fields. seed=-1 triggers `secrets.randbelow(2**53)` in the handler.
- **API:** `app/api/images.py` — POST sync handler (Pydantic → registry → workflow graph prep → adapter → S3 upload → response) + GET gateway (auth → job lookup → S3 fetch → stream). `app/api/models.py` — OpenAI-compatible `GET /v1/models`.
- **Wiring:** `app/main.py` lifespan extended to load registry + ensure bucket + instantiate adapter; exposes 4 items on app.state (store, registry, s3, adapter, keyset, public_base_url, async_mode_enabled, job_timeout_s). `public_base_url` scheme-validated.
- **Deps:** pyproject adds `boto3 + tenacity + PyYAML` runtime, `moto[s3]` dev. Also **fixed Cycle 2 latent bug**: added `httpx` to runtime deps (was dev-only despite being imported by the adapter).
- **Dockerfile:** adds `COPY migrations/ ./migrations/` — fixed a Cycle 1 latent bug (migrations never shipped in image, `jobs` table missing when running in container).
- **Compose:** service container now mounts `./config:/app/config:ro`, `./workflows:/app/workflows:ro`, `./models:/app/models:ro`, plus `IMAGE_GEN_PUBLIC_BASE_URL` passthrough.
- **Tests:** 23 validation + 13 registry + 9 storage + 17 sync + 6 gateway + 3 models + 1 integration.
- **Arch v0.6:** §4.6 rewritten (backend gateway replaces presign — one boto3 client, no `S3_PUBLIC_ENDPOINT`, no `PRESIGN_TTL_S`); §6.1 response URL format; new §6.1.1 gateway endpoint spec; §11 adds data-at-rest posture note + gateway-auth posture note; §20 change log entry.

**Decisions locked in CLARIFY:**

- **Gateway model replaces presigned URLs** (Q4) — unified Bearer auth for create + fetch, exact fetch observability for the Cycle 4 orphan reaper, no SigV4 Host-header shenanigans. Bandwidth amplification acceptable at LoreWeave's scale.
- **LoreWeave client timeout is not our concern** (Q1) — server-side uses `JOB_TIMEOUT_S=300` + `size_max_pixels=1572864`; if their client gives up sooner, that's their config.
- **MinIO bucket init via lifespan** (Q2) — `S3Storage.ensure_bucket()` idempotent, fail-fast if MinIO unreachable at startup.
- **b64_json response format supported** (Q6) — always upload to S3 (orphan reaper invariant), plus inline base64 when requested.
- **PNG magic pre-upload validation** (Q7) — cheap defensive guard against ComfyUI weirdness.

**Bugs caught during BUILD:**

- **Cycle 2 latent: `httpx` missing from runtime deps.** Imported by the adapter, only listed under dev. Service container built at Cycle 0 never had it. Added to runtime.
- **Cycle 1 latent: `migrations/` not copied into Docker image.** `jobs` table never created inside container; only surfaced now because /health doesn't query jobs. Added `COPY migrations/` to Dockerfile.
- **Compose mounts missing `config/workflows/models` on service container.** Registry startup validation failed with `yaml_missing`. Added three `:ro` mounts.
- **`adapter._client_id` private-member leak into handler.** Promoted to public `adapter.client_id`; updated callers.
- **Workflow `ckpt_name` + `vae_name` included subdir prefix.** ComfyUI's `CheckpointLoaderSimple.ckpt_name` is already scoped to `models/checkpoints/` (same for `VAELoader` and `models/vae/`); prefix causes 400 `value_not_in_list`. Stripped.
- **Adapter misclassified `400 + node_errors` as `ComfyUnreachableError`.** Reclassified as `ComfyNodeError` (client bug, not unreachable backend).

**`/review-impl` pass found 12 findings, all fixed in the same cycle:**

- **MED-1** `seed=-1` was deterministically producing identical images every call (hardcoded 0). Now `secrets.randbelow(2**53)` on -1, with the resolved seed persisted in `result_json` so callers can reproduce. OpenAI API convention restored.
- **MED-2** `JOB_TIMEOUT_S` env plumbed into the handler via `app.state.job_timeout_s`. Previously hardcoded 300s regardless of env.
- **MED-3** S3 retry limited to `_TRANSIENT_S3_CODES` (ServiceUnavailable/SlowDown/Throttling/RequestTimeout/InternalError/OperationAborted). Permanent errors (AccessDenied, NoSuchBucket, InvalidAccessKeyId) fail fast with 1 call instead of burning 6+ seconds on retries that can't succeed.
- **LOW-4** Registry validates `defaults.sampler` / `defaults.scheduler` against `ALLOWED_SAMPLERS`/`ALLOWED_SCHEDULERS` at load time. Typos in YAML fail startup, not request time.
- **LOW-5** Registry detects duplicate `name` entries, raises `RegistryValidationError("duplicate_name", ...)`.
- **LOW-6** Registry validates `prediction` against `{eps, vpred}` and `backend` against `{comfyui}` — Python `Literal` type annotations are ignored at runtime, so typos slip through without explicit checks.
- **LOW-7** `IMAGE_GEN_PUBLIC_BASE_URL` scheme-validated at lifespan — must start with `http://` or `https://`. Prevents emitting bogus URLs like `invalid-no-scheme/v1/images/...`.
- **LOW-8** New `tests/test_models_endpoint.py` covers auth + shape + either-scope paths for `GET /v1/models`.
- **LOW-9** Arch §11 gains paragraphs on jobs-table plaintext (prompts on disk) + gateway-auth posture (no per-key job ownership; ksuid makes enumeration impractical but not impossible).
- **LOW-10** New test for `b64_json` with `n=2` (gap: existing coverage was only n=1 for b64 and n=2 for url).
- **COSMETIC-11** Empty-string on sampler/scheduler now rejected (Pydantic `min_length=1`), consistent with prompt's min_length=1. Empty is a client bug, not a signal to use defaults.
- **COSMETIC-12** Handler logs `sync.multiple_latent_nodes` warning if >1 `EmptyLatentImage` is found in the graph (defensive for future multi-stage workflows in Cycle 5/7).

**Live verification:**

```
pytest (full)     158/158 pass (155 unit + 3 integration)
ruff              clean
ruff format       clean
POST /v1/images/generations → 200 + {url: http://127.0.0.1:8700/v1/images/gen_<ksuid>/0.png}
GET  /v1/images/<ksuid>/0.png → 200 + Content-Type: image/png + valid PNG (512×512, 257 KB)
/v1/models        {"object":"list","data":[{"id":"noobai-xl-v1.1", ...capabilities, backend}]}
service lifespan  registry.loaded + s3.ensure_bucket.ok + service.started logged at INFO
```

### Retro — lessons worth keeping

- **Dev-only deps are a trap.** `httpx` was fine in test (pulled in via `httpx` → dev dep → venv). Fine in `uv run pytest`. Not fine in the Docker image, which installs `--no-dev`. Every `import x` in `app/` must have `x` in runtime deps. Tie this to a CI lint: `python -c "import app.main"` against a `--no-dev` venv catches it every time. Same gotcha applied to Cycle 1's migrations/ — file existed on disk, tests found it, but container image didn't include it.
- **`Literal` type annotations are comments at runtime.** `backend: Literal["comfyui"]` doesn't reject `"local"` — Python accepts any value. For config-loaded fields, always pair the annotation with an explicit membership check (`_ALLOWED_BACKENDS = frozenset(...)`). Same trap for `prediction`. Annotations inform IDE + mypy; they don't defend the runtime.
- **OpenAI's `seed=-1` convention is load-bearing.** Callers assume -1 means random, not 0. Our initial translation `job.seed if job.seed >= 0 else 0` silently broke that contract. Fix was three lines but the bug would have become a flood of "all my images look identical" bug reports. Whenever a public API has a sentinel value (`null`, `-1`, `"auto"`), trace what it means all the way to the underlying system — not just "it's valid per the regex".
- **Retry too broad is worse than retry too narrow.** `retry_if_exception_type(ClientError)` retries on AccessDenied. Six wasted seconds per request before the user sees the error. Enumerate the transient-only codes explicitly; be willing to add one later if you discover a new transient, rather than start with "everything retries".
- **Background monitor pattern + pytest output capture can silently drop stdout on Windows.** Ran pytest 3-4 times before realizing the background task was writing to a 0-byte file (not a pytest crash, just stdout capture weirdness with `run_in_background`). Fix: redirect to a real file (`> /tmp/pytest_out.txt 2>&1`), then use Monitor to watch the file. Saves 10 minutes of "is it still running?".

**What's next (Sprint 7 plan / Cycle 4):**

1. `app/queue/worker.py` — single asyncio task, pulls Job from `asyncio.Queue`, serial GPU execution, `MAX_QUEUE` bounded.
2. `app/queue/orphan_reaper.py` — background task, TTL-driven, deletes S3 objects for completed-but-never-fetched jobs.
3. `app/queue/recovery.py` — boot scan: `queued` → re-enqueue, `running` → `failed{service_restarted}` with terminal state flushed so async pollers get an answer.
4. `app/api/images.py` extensions — sync handler enqueues instead of calling adapter directly; watches `Request.is_disconnected()` via `asyncio.shield`; on disconnect sets `mode=async, webhook_handover=true`.
5. Tests: `test_queue_worker.py`, `test_disconnect.py`, `test_restart_recovery.py`.

**Prerequisites for Cycle 4:** none external. Plan's unknowns table shows none for Cycle 4.

**Commits this sprint:** 1 feat + 1 docs session-close.

---

## Sprint 5 — 2026-04-19 — Cycle 2 ComfyUI sidecar + adapter + anchor-tagged workflow complete

**Outcome:** Real ComfyUI running in a sibling container (pinned to v0.9.2 + city96/ComfyUI-GGUF commit 6ea2651) generates a PNG from NoobAI-XL v1.1 via our BackendAdapter in ~27s on the RTX 4090. 87 tests green (85 unit + 2 integration), ruff + format clean. Arch v0.5 amendment landed covering two CLARIFY-surfaced deviations from the original spec. Ready for Cycle 3 (MinIO + first sync HTTP endpoint).

**Files created / modified (23):**

- **Sidecar image:** `docker/comfyui/Dockerfile` (CUDA 12.4.1-runtime, Python 3.11, uv for pip, non-root comfy user, `HEALTHCHECK` via /system_stats, build-time textual smoke test for GGUF node classes), `docker/comfyui/custom-nodes.txt` (pin source of truth, committed), `docker/comfyui/entrypoint.sh`.
- **Backend stack:** `app/backends/base.py` (`BackendAdapter` Protocol, `GenerationResult`, `ModelConfig`, error hierarchy mapping to arch §13 codes), `app/backends/comfyui.py` (HTTP + WebSocket, one-retry-then-poll state machine, /interrupt + /free, status_str discriminator).
- **Registry:** `app/registry/workflows.py` (`load_workflow`, `validate_anchors`, `find_anchor` with comma-separated multi-anchor convention).
- **Workflow:** `workflows/sdxl_eps.json` — anchor-tagged NoobAI v1.1 SDXL workflow (7 anchors).
- **Tests:** `tests/test_anchor_resolver.py` (11), `tests/test_comfyui_adapter.py` (22 mocked, up from 14 after /review-impl fixes), `tests/integration/test_comfyui_adapter.py` (2 real-GPU).
- **Wiring:** `docker-compose.yml` (swap nginx placeholder for real comfyui build, GPU reservation, `./models:/workspace/ComfyUI/models:ro` full-tree mount, `pull_policy: never`), `pyproject.toml` (+websockets, +respx), `.env.example` (+COMFY_POLL_INTERVAL_MS, +JOB_TIMEOUT_S).
- **Arch v0.5:** §20 change log, §8 model roster (v1.1 replaces Vpred-1.0), §4.4 example (`checkpoints/NoobAI-XL-v1.1.safetensors` + `vae/sdxl_vae.safetensors`), §5 topology (unified `./models` mount), §9 vpred-deferred note.
- **Cleanup:** deleted `docker/comfyui-placeholder/`, reclaimed 7.1 GB of HuggingFace download extras from `./models/`.

**Decisions locked in CLARIFY:**

- **NoobAI-XL v1.1 (eps)** replaces Vpred-1.0 as the day-1 SDXL model — simpler workflow (no ModelSamplingDiscrete injection), better tool compatibility with default SDXL samplers, NoobAI team's current stable. vpred injection code deferred indefinitely (arch §9 note).
- **`./models:/workspace/ComfyUI/models:ro`** mounts the full ComfyUI models tree rather than just checkpoints/, so external VAE files resolve via `models/vae/<name>.safetensors`.
- **One WS reconnect → polling fallback** per CLARIFY Q4. Single `client_id` per adapter instance per arch §4.3, filtered by `prompt_id` on WS events.
- **ComfyUI + GGUF pins** captured in `custom-nodes.txt` + Dockerfile ARG defaults + compose build.args (three-way consistency required on bumps).

**Bugs caught during BUILD:**

- **Dockerfile smoke test import failed** — `ComfyUI-GGUF` folder has a hyphen (invalid Python module name). Switched from `from ... import` check to `grep` check on node class names.
- **Workflow `ckpt_name` had directory prefix** — ComfyUI's `CheckpointLoaderSimple.ckpt_name` expects a name relative to `models/checkpoints/`, not including that prefix. Same for `vae_name`.
- **Adapter misclassified `400 + node_errors`** as `ComfyUnreachableError` — ComfyUI returns 400 (not 200) when the graph fails validation. Reclassified to `ComfyNodeError`.

**`/review-impl` pass found 11 findings, all fixed in the same cycle:**

- **MED-1** `_try_connect` now catches the full `websockets.exceptions.WebSocketException` hierarchy + `TimeoutError` (handshake errors, protocol violations, open-handshake timeouts all route to polling fallback).
- **MED-2** `free()` loop-verifies VRAM rose per spec §11.3 (baseline reading, POST /free, poll /system_stats up to verify_timeout_s looking for increase; log.warning if it doesn't rise).
- **MED-3** `wait_for_completion` raises `RuntimeError` on duplicate `prompt_id` registration instead of silently overwriting the first waiter's future (Cycle 4 queue/disconnect-handler risk).
- **MED-4** `_poll_until_done` + `fetch_outputs` both invoke new `_raise_if_errored` helper that checks `status.status_str != "success"` — previously both paths silently returned "success" on failed jobs because `completed=True` is set for both terminal states.
- **LOW-5** `cancel()` raises `ComfyUnreachableError` on /queue non-200 instead of silently no-op-ing (treated as "neither running nor pending").
- **LOW-6** `__init__` validates `poll_interval_ms > 0` — prevents accidental tight-loop CPU burn from misconfigured poll intervals.
- **LOW-7** `submit()` catches `TypeError` on non-JSON-serializable graph values → `ComfyNodeError("not JSON-serializable: ...")` instead of raw TypeError escaping.
- **LOW-8** Pin-source-of-truth comment on Dockerfile + compose — explicitly names `custom-nodes.txt` as canonical; all three locations must bump together.
- **LOW-9** Dockerfile `COPY --from=ghcr.io/astral-sh/uv /uv /uv /usr/local/bin/` → `/uv /uvx /usr/local/bin/` (typo fix; `uvx` is a separate binary the uv image ships).
- **COSMETIC-10** Renamed `test_fetch_outputs_reads_output_anchored_nodes` → `test_fetch_outputs_reads_all_image_nodes` to reflect actual semantics (ComfyUI's /history doesn't echo `_meta` back; anchor filtering isn't possible there).
- **COSMETIC-11** `pull_policy: never` on comfyui service suppresses the failing "pull access denied" log on every `docker compose up`.

**Live verification:**

```
pytest (full)      87/87 pass
ruff               clean
ruff format        clean
docker build       image-gen-comfyui:0.9.2 (4.7 GB)
docker compose ps  comfyui healthy within 120 s start-period
integration test   PNG bytes from NoobAI v1.1, PNG magic verified, 27 s warm
/system_stats      RTX 4090 visible, 22.3 / 24 GB VRAM free
```

### Retro — lessons worth keeping

- **ComfyUI `ckpt_name` / `vae_name` are scoped-by-subdir automatically — don't include the subdir prefix in the workflow JSON.** `CheckpointLoaderSimple` only accepts names from `get_filename_list("checkpoints")`, which returns names relative to `models/checkpoints/`. The full-tree `./models:/workspace/ComfyUI/models:ro` mount gave us the subdirs, but workflow paths stayed subdir-less. Any future workflow edit has to obey this. Adapter error was ComfyUnreachableError → misleading; fix on both sides (adapter reclassify + workflow prefix strip) caught during integration test.
- **`custom_nodes/ComfyUI-GGUF` has a hyphen in the folder name.** Python won't import it directly; ComfyUI loads it via path manipulation at runtime. Our build-time smoke test switched to a textual `grep` on the node class names in `nodes.py` rather than a Python import. Any custom node with a hyphenated folder hits this.
- **`BaseHTTPMiddleware` taint carries forward.** Cycle 1's retro flagged it; Cycle 2 made sure the new `ws_connect` factory + `_ws_reader` task lived outside any middleware. The pattern "inject the factory as a keyword arg, default to real impl" also gives us a clean seam for test mocks without `patch()` globals.
- **`httpx.ASGITransport` and `websockets.connect` both need wrangling in tests.** httpx: `raise_app_exceptions=False` (Cycle 1). websockets: inject a factory that returns a `FakeWS` backed by `asyncio.Queue`. The Queue pattern gives deterministic event delivery timing — tests can `await _push(ws, event)` and then assert within the same turn.
- **ComfyUI's `history[pid].status.completed == True` is set for both success AND error terminals.** Must discriminate via `status_str`. Polling-fallback path would silently return "success" on failed jobs without this. The discriminator shape lives in a shared `_raise_if_errored` helper so both `_poll_until_done` and `fetch_outputs` use the same check.

**What's next (Sprint 6 plan / Cycle 3):**

1. `app/storage/s3.py` — two boto3 clients (internal vs public endpoint), `upload_png(job_id, index, bytes)`, `presign_get(bucket, key, ttl)` wrapped in `tenacity` retry.
2. `app/registry/models.py` + `config/models.yaml` — load the model registry, validate startup (files exist, anchors present, VRAM ≤ budget).
3. `app/api/images.py` — `POST /v1/images/generations` sync path: validate → resolve model → load workflow → overwrite prompt/sampler params → `adapter.submit()` → `wait_for_completion` → `fetch_outputs` → upload → presign → respond.
4. `app/api/models.py` — `GET /v1/models` reading from the registry.
5. `app/validation.py` — Pydantic request model enforcing arch §6.0 bounds (minus webhook fields until Cycle 9).
6. Tests: `test_model_registry.py`, `test_sync_endpoint.py` (mocked adapter + S3), `test_e2e_sync.py` (integration — real ComfyUI + real MinIO).

**Prerequisites for Cycle 3:**
- LoreWeave's HTTP client timeout for sync path (plan unknowns §Cycle-3) — need from @letuhao1994 before drafting the Pydantic `size_max_pixels` rule.
- MinIO bucket creation: needs a startup init (either via entrypoint or a separate `mc` admin step).

**Commits this sprint:** 1 expected for Cycle 2 code + 1 for session close.

---

## Sprint 4 — 2026-04-19 — Cycle 1 auth + SQLite + structured logging complete

**Outcome:** Every request to the service now carries a JSON-structured log line with `request_id`, every job is persistable via `JobStore` CRUD through arch §4.2's full schema, and `/health` has a boolean-vs-verbose shape gated by Bearer auth. 53 pytest cases green, ruff + format clean, in-container smoke confirms JSON logs + envelope responses. Ready for Cycle 2 (ComfyUI sidecar + adapter).

**Files created / modified (20):**

- `app/auth.py` — multi-key parser, kid derivation, `hmac.compare_digest`, FastAPI deps with contextvars binding, public `verify_key` helper.
- `app/errors.py` — error-envelope handler covering both `StarletteHTTPException` (404s/405s) and generic `Exception` (500s).
- `app/logging_config.py` — structlog + stdlib bridge, recursive `redact_sensitive` processor that drops sensitive keys at any nesting depth and regex-scrubs `Bearer`/`X-Amz-Signature`/`Authorization:` from the `event` + `exception` strings.
- `app/middleware/logging.py` — **pure ASGI** `RequestContextMiddleware` (not `BaseHTTPMiddleware`): binds `request_id`, echoes header, logs access line with float `duration_ms`.
- `app/queue/store.py` — `JobStore` class (connect/close/write/read/healthcheck), `apply_migrations` with strict `NNN_<name>.sql` filename enforcement.
- `app/queue/jobs.py` — `Job` dataclass, CRUD via `INSERT ... RETURNING` (one round-trip), transition guard with `InvalidTransitionError` + `JobNotFoundError`.
- `app/api/health.py` — DB-probing `/health`, 503 on unreachable, auth-gated verbose shape.
- `app/main.py` — rewrote with lifespan (configure_logging → JobStore.connect → keyset load), error envelope install, pure-ASGI middleware mount.
- `migrations/001_init.sql` — arch §4.2 jobs schema + schema_version tracking table + two indexes for Cycle 4 reapers.
- `docker-compose.yml` — added `./data:/app/data` bind mount + `API_KEYS`/`ADMIN_API_KEYS`/`LOG_LEVEL`/`LOG_PROMPTS`/`DATABASE_PATH` env.
- `.env.example` — +3 vars (`LOG_LEVEL`, `LOG_PROMPTS=false`, `DATABASE_PATH`).
- `pyproject.toml` — added `aiosqlite`, `structlog`, `svix-ksuid` to runtime deps.
- Tests (new/updated): `tests/test_auth.py` (16), `tests/test_job_store.py` (13), `tests/test_logging.py` (14), `tests/test_health.py` (10 updated), `tests/conftest.py` (per-test DB + broken-DB fixture, `raise_app_exceptions=False`).
- Docs: `docs/specs/2026-04-19-cycle-1-fastapi-auth-sqlite-logging.md` (spec + design §12), `docs/plans/2026-04-19-cycle-1-tasks.md` (6-chunk task plan).

**Decisions locked:**

- **structlog over stdlib.** `contextvars.merge_contextvars` processor gives automatic `request_id`/`key_id`/`job_id` propagation across async hops; rolling this in stdlib would have meant hand-rolling a `ContextVar[dict]` + custom formatter.
- **SQLite posture:** `WAL + synchronous=NORMAL + busy_timeout=5000 + foreign_keys=ON`, single long-lived connection, `asyncio.Lock` guarding writes. Reader-writer split deferred to Cycle 4 if contention surfaces.
- **Prompt logging off by default** (`LOG_PROMPTS=false`) and further gated to DEBUG level — neither flag alone is enough.
- **Kid width 8 hex chars** — documented birthday bound at ~65k distinct keys; sufficient for the roadmap, flagged for reconsideration if ever multi-tenant.
- **Pure ASGI middleware** (not `BaseHTTPMiddleware`) — required to keep FastAPI's exception-handler chain working cleanly.

**`/review-impl` pass found 10 findings, all fixed in same cycle:**

- MED-1: 401 responses now carry `WWW-Authenticate: Bearer` (RFC 7235 §3.1).
- MED-2: Bearer scheme comparison is now case-insensitive (RFC 6750 §2.1) — accepts `Bearer` / `bearer` / `BEARER`.
- MED-3: Added generic `Exception` handler to `install_error_envelope` so unhandled 500s carry `{"error":{"code":"internal",...}}` rather than FastAPI's default plain text.
- MED-4: `redact_sensitive` now walks dicts/lists recursively (strips sensitive keys at any depth, redacts prompts at any depth) and applies regex scrubs to the `event` + `exception` fields catching `Bearer <tok>`, `X-Amz-Signature=...`, `Authorization: ...` leaks via f-string templating or frame-local traceback rendering.
- LOW-5: 3 pytest cases for migration runner (bad filename rejection, duplicate prefix rejection, idempotent re-apply).
- LOW-6: Simplified the redundant dedupe check in `apply_migrations`.
- LOW-7: `duration_ms` is now a float with 3-decimal (µs) resolution — sub-millisecond requests no longer log as `0`.
- LOW-8: `create_queued` uses `INSERT ... RETURNING` (SQLite ≥ 3.35) — single round-trip instead of INSERT+SELECT.
- COSMETIC-9: `status='queued'` is parameter-bound in the INSERT, not inline literal.
- COSMETIC-10: `kid_for` docstring documents the 32-bit birthday bound and when to widen.

**Side effect of MED-3 fix:** uncovered a latent bug — `RequestContextMiddleware` was a `BaseHTTPMiddleware` subclass, which wraps each request in an anyio task group. The task group converts caught exceptions into `ExceptionGroup` and breaks FastAPI's exception-handler chain for unhandled errors. Rewrote as pure ASGI middleware. Simpler, faster (no context-switch per request), and correct.

**Live verification:**

```
pytest      53/53 pass
ruff        clean
ruff fmt    clean
curl /health (no auth)              {"status":"ok"}
curl /health (bearer probe-gen)     {"status":"ok","db":"ok"}
curl /health (Authorization: bearer …)   200 (case-insensitive scheme)
curl /nonexistent                   {"error":{"code":"not_found","message":"Not Found"}}
in-container log line               {"method":"GET","path":"/health","status":200,"duration_ms":0.088,"event":"request.served","request_id":"…"}
boot line                           {"event":"service.started","generation_keys":1,"admin_keys":1,"imagegen_env":"dev",…}
no double access log                one request.served per curl; uvicorn.access silenced
```

### Retro — lessons worth keeping

- **`BaseHTTPMiddleware` is a trap when the app has exception handlers.** It wraps requests in an anyio task group that converts caught exceptions into `ExceptionGroup`, which breaks FastAPI's handler chain for `Exception` — unhandled errors propagate to the test client instead of being converted to 500 responses. Only surfaced when we added the generic-exception test during /review-impl. For any middleware that isn't doing stream body transformation, prefer pure ASGI (`async def __call__(self, scope, receive, send)`). Save `BaseHTTPMiddleware` for middleware that *must* rewrite response bodies.
- **RFC correctness pays off under adversarial review, not before.** `WWW-Authenticate` on 401 and case-insensitive `Bearer` were trivial to add, but neither the spec nor the PO review surfaced them — /review-impl did. Budget time for RFC checks on every auth/HTTP surface.
- **Redaction processors that look at top-level keys only are inadequate.** A nested dict (e.g. request context in an error payload) with `{"Authorization": "Bearer foo"}` would have slipped through. The fix — recursive walk + string-level regex — is 20 LOC and catches a whole class of future leaks.
- **`ASGITransport(raise_app_exceptions=True)` is the wrong default for apps with exception handlers.** httpx re-raises any exception that transited the ASGI chain even when the handler converted it to a response. Our `conftest.py` now sets `raise_app_exceptions=False` so tests see the actual response body.
- **INSERT ... RETURNING is supported in SQLite 3.35+; Python 3.12 ships with 3.40+.** Worth using from the start — cuts a round-trip in every create-and-return CRUD helper.

**What's next (Sprint 5 plan / Cycle 2):**

1. Write `docker/comfyui/Dockerfile` pinned to a specific ComfyUI tag + `city96/ComfyUI-GGUF` commit, on `nvidia/cuda:12.x-runtime-ubuntu22.04`.
2. Create `workflows/sdxl_vpred.json` anchor-tagged for NoobAI-XL Vpred (`%MODEL_SOURCE%`, `%CLIP_SOURCE%`, `%POSITIVE_PROMPT%`, `%NEGATIVE_PROMPT%`, `%KSAMPLER%`, `%OUTPUT%`, + `ModelSamplingDiscrete` for vpred injection).
3. Build `app/backends/base.py` (Protocol) + `app/backends/comfyui.py` (HTTP + WebSocket adapter with poll fallback + `/interrupt` + `/free`).
4. Build `app/registry/workflows.py` (anchor validation + find-by-anchor).
5. Unit tests for anchor resolver; integration test `tests/integration/test_comfyui_adapter.py` (real GPU — skipped in CI).
6. Prereq: confirm `NoobAI-XL-Vpred-1.0.safetensors` + `sdxl_vae.safetensors` in `./models/` on the host.

**Commits this sprint:** 1 expected (all Cycle 1 + review-impl fixes).

---

## Sprint 3 — 2026-04-18 — Cycle 0 repo bootstrap complete

**Outcome:** `docker compose up` brings up three services on a private network (`image-gen-service`, `comfyui` placeholder, `minio`). `curl http://127.0.0.1:8700/health` returns `{"status":"ok"}`. Unit tests green (4/4), ruff clean, image size 285 MB. All three containers Docker-healthy. Ready for Cycle 1 (auth + SQLite + structured logging).

**GPU toolkit check (per plan prerequisite):** passed. RTX 4090 visible, driver 581.80, CUDA 13.0 inside containers. **Flag for Cycle 7:** 17.2 / 24 GB VRAM in use on host before load — only ~7 GB free. Chroma Q8's 9 GB floor will exceed budget unless something is freed before Cycle 7.

**Files created (14):**

- `pyproject.toml`, `.python-version`, `.dockerignore`, `.env.example`, `.pre-commit-config.yaml`
- `Dockerfile` (two-stage: builder with uv → runtime without), `docker/entrypoint.sh`
- `docker-compose.yml`, `docker-compose.override.yml.example`, `docker/comfyui-placeholder/default.conf`
- `app/__init__.py`, `app/main.py`
- `tests/__init__.py`, `tests/conftest.py`, `tests/test_health.py`
- `README.md` rewritten from a one-liner to a real README

Also: `.gitignore` extended with runtime-data paths (`data/`, `minio-data/`, `models/`, `loras/`, `docker-compose.override.yml`).

**`/review-impl` pass found 14 findings, all fixed in v0.1.1:**

- HIGH: MinIO healthcheck curl-only → curl-with-wget-fallback; Python pin excluded 3.13 host → relaxed to `<3.14` + `.python-version` pins image to 3.12.
- MED: two-stage Dockerfile drops ~30 MB of uv dead weight (315 → 285 MB); `entrypoint.sh` propagates `SHUTDOWN_GRACE_S` to `uvicorn --timeout-graceful-shutdown`; `depends_on: comfyui: service_healthy`; ruff `S104` ignore + policy comment; `.dockerignore` symmetric on override files; HEAD + Content-Type tests (also discovered `@app.get` doesn't auto-register HEAD, fixed with `api_route`).
- LOW: Dockerfile `HEALTHCHECK` directive using `urllib.request` (no extra binary); `asgi-lifespan.LifespanManager` in `conftest.py` so future startup hooks fire in tests; `.env.example` `API_KEYS=` blank for fail-closed posture; forward-compat deps documented per-cycle.

**Live verification:**

```
pytest    4/4 pass
ruff      clean
curl /health    {"status":"ok"}
compose ps      all three services Up (healthy)
PID 1 in container    uvicorn ... --timeout-graceful-shutdown 90
```

**Dev-env note:** moved MinIO dev ports to 127.0.0.1:9100/9101 because `free-context-hub-minio-1` already occupies 9000/9001 on this host. Base compose still uses internal port 9000.

### Retro — lessons worth keeping

- **`/review-impl` on a trivial cycle still catches material issues** — 14 findings on what I'd called "boilerplate". Two were genuine HIGHs (MinIO healthcheck, Python pin). Author blindness is real even on config files. Budget ~5–10 min per cycle for the adversarial pass.
- **`@app.get` in FastAPI does NOT auto-register HEAD.** Reviewer flagged HEAD coverage gap; writing the test revealed FastAPI's actual behavior. Use `api_route(methods=["GET","HEAD"])` for probe endpoints.
- **Python pin ceilings are load-bearing.** `>=3.12,<3.13` appeared conservative but actually excluded the dev host (3.13). Relax ceilings to match reality, pin the *floor* via `.python-version` for Docker determinism.
- **First `uv sync` in Docker will try to build the project unless `--no-install-project`.** Hatchling demands README.md at build time even if it's not copied yet. The `--no-install-project + PATH=.venv/bin` pattern is the clean way to avoid that.

**What's next (Sprint 4 plan):**

1. Start Cycle 1 — `app/auth.py` (multi-key parser, constant-time compare), `app/queue/store.py` (aiosqlite layer + migration 001), `app/middleware/logging.py` (JSON logs + correlation id), `app/api/health.py` (deep probe of DB + dependents).
2. Tests: `test_auth.py`, `test_job_store.py`, updated `test_health.py` for dependent-down → 503.
3. Still no image generation in Cycle 1 — that's Cycle 2.

**Commits this sprint:** 1 expected (all Cycle 0 + Cycle 0 v0.1.1 fixes).

---

## Sprint 2 — 2026-04-18 — Implementation plan written (11 cycles)

**Outcome:** Decomposed the v0.4 architecture into 11 vertical-slice cycles with explicit dependencies, per-cycle files/tests/verification/descope, and an owner-assigned unknowns table. Each cycle is independently runnable through the 12-phase workflow. Ready to CLARIFY Cycle 0.

**New files:**

- `docs/plans/2026-04-18-image-gen-service-build.md` — 569 lines, 11 cycles + LoreWeave integration PR + dependency graph + per-cycle prereqs table.
- `docs/session/SESSION.md` — Sprint 1 retro lessons appended (post-Sprint-1 commit, landing now).

**Cycle shape decisions:**

- Vertical slices only — each cycle ships a thing that works end-to-end, never a horizontal layer.
- Every cycle has an explicit descope list — scope-creep catcher.
- Cycle 9 (webhook dispatcher) is sized XL alone — the v0.4 hardening is dense enough to warrant its own sprint, subagent dispatch recommended.
- Cycle 11 (LoreWeave PR) is parallel, soft-blocks only Cycle 10's prod-mode acceptance test.

**What's next (Sprint 3 plan):**

1. Verify Docker Desktop + NVIDIA Container Toolkit on the Win11 host (listed as Cycle 0 prerequisite in the plan).
2. Begin Cycle 0 (repo bootstrap) — reset workflow, classify size S, run phases through to commit.
3. In parallel, start drafting the LoreWeave integration-guide amendment (Cycle 11) so it lands before Cycle 9 tests.

**Commits this sprint:** 1 expected (plan doc + Sprint 1 retro tail that didn't make the Sprint 1 commit).

---

## Sprint 1 — 2026-04-18 — Agentic workflow installed + architecture v0.4 drafted

**Outcome:** Project bootstrapped with a 12-phase agentic workflow enforcement layer; architecture spec for the image-generation service written and reviewed through four adversarial passes (initial draft, 4-perspective review, `/review-impl` on webhook surfaces, fix-all response). Ready for BUILD to begin.

**New / modified files:**

- `scripts/workflow-gate.sh` — installed; patched to prefer `python` over pyenv-win's broken `python3` shim that mangles multi-line `-c`.
- `.claude/settings.json` — pre-commit hook blocks commits without VERIFY + POST-REVIEW + SESSION.
- `.claude/commands/review-impl.md` — on-demand adversarial review command.
- `CLAUDE.md` — full workflow pasted in.
- `.gitignore` — `.workflow-state.json` added.
- `docs/architecture/image-gen-service.md` — draft v0.1 → v0.2 → v0.3 → **v0.4**, 1276 lines, 20 sections.
- `docs/session/SESSION.md` — this file.

**Review rounds and what changed:**

1. **v0.1 → v0.2 (four-perspective review).** Four parallel agents (architect, security, QA, dev) surfaced 7 convergent HIGHs + 3 unique HIGHs + ~10 MEDs + ~5 LOWs. Biggest fixes: SQLite job store (not in-memory), ComfyUI anchor-node convention, 11-rule Civitai fetch hardening, prod network posture with startup assertion, full ComfyUI prompt-API contract (`client_id`, WebSocket, `/interrupt` + `/free` + `DELETE /queue`).
2. **v0.2 → v0.3 (webhook addition).** User requested webhook notifications. Added §4.8 dispatcher (separate asyncio task, HMAC signing, 5-attempt retry, persistent queue), `webhook_deliveries` SQLite table, `/v1/webhooks/deliveries/{id}` admin endpoint, integration-guide expansion.
3. **v0.3 → v0.4 (`/review-impl` on webhook surfaces).** Dedicated adversarial reviewer found 14 findings on webhook signing, SSRF, and sync/dispatcher state-machine. All resolved: DNS pinning, IP-range filter, no-redirect policy, Stripe-style timestamp+body signing (`t=<ts>,v1=<hex>` header, 300 s skew), multi-secret rotation (`WEBHOOK_SIGNING_SECRETS` plural set), TOCTOU re-validation per attempt, `webhook_handover` SQLite barrier, fail-closed allowlist (deny-all by default), `IMAGEGEN_ENV={prod,dev}` explicit mode switch, literal Go receiver reference implementation.

**Decisions locked:**

- Runtime: ComfyUI sidecar (separate container), not embedded.
- Day-1 models: NoobAI-XL Vpred-1.0 (SDXL, ~7 GB VRAM) + Chroma1-HD Q8 GGUF (~9 GB VRAM); stay under 12 GB budget (50 % of RTX 4090).
- Storage: MinIO locally → real S3 on Novita, swap via env.
- Queue: in-process asyncio, **one GPU worker** for v1, scale later.
- Async mode gated off (`ASYNC_MODE_ENABLED=false`) until integration-guide amendment lands.
- LoRAs: local directory scan + Civitai fetch with 11-rule hardening.
- Auth: multi-key set with `kid` logging; admin scope separate from generation scope.
- Webhook delivery: terminal events only, at-least-once, HMAC-SHA256 with timestamp binding, durable receiver dedupe required.

**Out of scope (deferred, §17 in arch doc):** HF repo paths for pre-download script, SSE progress streaming, img2img / inpaint / ControlNet (v2), per-caller quota, Prometheus/OTel, multi-hash Civitai verify, sidecar trust re-verify, ComfyUI zero-downtime custom-node swap.

**Owner dependencies (external to this repo):**

- @letuhao1994 to land the LoreWeave integration-guide amendment PR covering: async mode, webhook field, Go receiver reference implementation, durable dedupe mandate, timestamp freshness rule, suggested `POST /v1/webhooks/image-gen` route.

**What's next (Sprint 2 plan):**

1. Pick the BUILD order — probably vertical slice: FastAPI skeleton + auth + SQLite job store + sync endpoint + ComfyUI adapter (HTTP + WS) + NoobAI workflow template + MinIO upload. No webhook, no LoRA injection, no async, no Civitai fetch in sprint 2.
2. Write the custom `docker/comfyui/Dockerfile` with pinned ComfyUI + `ComfyUI-GGUF` + T5 encoder.
3. Write `workflows/sdxl_vpred.json` with anchor-tagged nodes (`%MODEL_SOURCE%`, `%CLIP_SOURCE%`, `%KSAMPLER%`, `%OUTPUT%`).
4. Confirm Docker Desktop + NVIDIA Container Toolkit is working on Win11 host before BUILD.
5. Verify LoreWeave's HTTP client timeout for sync path (operational risk #1 from pre-BUILD concerns).

**Commits this sprint:** 1 expected (this sprint's doc + workflow scaffold).

### Retro — lessons worth keeping

- **`/review-impl` caught 14 real webhook issues that the earlier 4-agent parallel review missed on the same surfaces.** The parallel review was broad; `/review-impl` went narrow + adversarial. Both are needed — they don't substitute. Author blindness persisted across the four roles because all four implicitly trusted that "a webhook is a webhook, signing is signing"; the dedicated adversarial pass asked "what specifically is `sha256=<hex>` vs `t=<ts>,v1=<hex>`, what DNS pins, what redirects?" and found real gaps. **Lesson:** on safety-sensitive surfaces, budget for both a broad review AND a focused adversarial pass.
- **`python3` on pyenv-win mangles multi-line `-c` arguments** (injects what looks like Windows batch noise). Fix: `scripts/workflow-gate.sh` now prefers `python` first, tests multi-line capability, falls back. If we ever hit this pattern in the service code too (we probably won't — the service uses stdlib subprocess, not ad-hoc `python -c`), the lesson is: detect capability, don't trust the name.
- **v0.1 doc was written without counting anchors.** The ComfyUI adapter section said "find LoraLoader by convention" without picking the convention. Ended up introducing `_meta.title = "%ANCHOR%"` in v0.2 as a formal pattern. **Lesson:** if you catch yourself writing "by convention" without naming the convention, you haven't finished the design.

