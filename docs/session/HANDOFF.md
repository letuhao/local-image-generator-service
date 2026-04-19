# Handoff — next session start here

> This file is **overwritten** every session close. It reflects **current state**, not history.
> History lives in [SESSION.md](SESSION.md). Architecture lives in [docs/architecture/image-gen-service.md](../architecture/image-gen-service.md). Build plan lives in [docs/plans/2026-04-18-image-gen-service-build.md](../plans/2026-04-18-image-gen-service-build.md).

**Last updated:** 2026-04-19 — Session closed after Sprint 6 / Cycle 3.

---

## Where we are

- **Branch:** `main`, **5 commits ahead** of `origin/main` (not pushed).
- **Commits since origin:**
  - `9b13ea9` feat(cycle-1): FastAPI auth + SQLite job store + structured JSON logging
  - `08e67aa` docs: session close — rewrite HANDOFF.md for Cycle 2 pickup
  - `2ff43a7` feat(cycle-2): ComfyUI sidecar + BackendAdapter + anchor-tagged NoobAI workflow
  - `b053021` docs: session close — rewrite HANDOFF.md for Cycle 3 pickup
  - `f9713aa` feat(cycle-3): MinIO gateway + model registry + POST /v1/images/generations
- **Plan progress:** 4 / 11 cycles complete.

```
[x] 0  Repo bootstrap
[x] 1  FastAPI + auth + SQLite + logging
[x] 2  ComfyUI sidecar + adapter + NoobAI workflow
[x] 3  MinIO gateway + model registry + first sync endpoint
[ ] 4  Queue + disconnect + reaper + restart                ← NEXT (M, 1-day budget)
[ ] 5  LoRA local + injection
[ ] 6  Civitai fetcher hardened
[ ] 7  Chroma model #2
[ ] 8  Async + polling
[ ] 9  Webhook dispatcher
[ ] 10 Startup validation + smoke test
[ ] 11 LoreWeave integration-guide PR (parallel, user-owned)
```

- **Workflow state:** retro pending close (will close with this commit).
- **Test suite:** `uv run pytest -q` → 158 passed (155 unit + 3 integration).
- **Arch version:** v0.6 (backend gateway for image fetch).

---

## Next action (Sprint 7 = Cycle 4)

**Goal per plan §Cycle 4:** sync requests queue behind a single worker; client disconnect mid-request doesn't orphan a blob; process restart mid-job produces a terminal status for the client (not a 404).

Files to create:

- `app/queue/worker.py` — single `asyncio.Task`, pulls `Job` from `asyncio.Queue` bounded by `MAX_QUEUE` (default 20 per arch §12). Updates job status (`queued`→`running`→`completed`/`failed`), drives the adapter (submit → wait → fetch), calls S3 upload. On queue-full → `429 queue_full`. Arch §4.2.
- `app/queue/orphan_reaper.py` — background `asyncio.Task`, `ORPHAN_REAPER_TTL` (default 24h per arch §4.2). Scans `jobs` for `status='completed' AND response_delivered=false AND updated_at < now - TTL` (plus the gateway-fetched flag we'll add), deletes S3 objects for those jobs.
- `app/queue/recovery.py` — on lifespan startup scan `jobs` for `status IN ('queued','running')`. `queued` → re-enqueue. `running` → `failed{error_code="service_restarted"}`, emit terminal status so Cycle 9's webhook dispatcher sees it.
- `app/api/images.py` updates — sync handler now enqueues + awaits future inside `asyncio.shield`, watches `Request.is_disconnected()`. On disconnect → `mode=async, webhook_handover=true` (Cycle 9 picks it up). On completion, set `response_delivered=true` after the response flushes.
- Tests: `tests/test_queue_worker.py`, `tests/test_disconnect.py`, `tests/test_restart_recovery.py`.

Schema changes needed (migration `002_*.sql`):
- None explicit — the `response_delivered`, `webhook_handover` columns already ship in `migrations/001_init.sql`. But we may add a `fetched_at` column tracking when the gateway last served the image, for the orphan reaper to key on "never-fetched" precisely. Decide in CLARIFY.

**Kickoff commands:**
```bash
cd d:/Works/source/local-image-generator-service
bash scripts/workflow-gate.sh reset
# Plan says Cycle 4 is M (~7 files). Script enforces; likely L.
bash scripts/workflow-gate.sh size M 7 5 1
bash scripts/workflow-gate.sh phase clarify
```

---

## Open items to resolve during Cycle 4 CLARIFY

- **Orphan-reaper "never fetched" signal.** Options:
  - A) Add `fetched_at TEXT NULL` column + migration `002_*.sql`; gateway updates it on first fetch.
  - B) Rely solely on `response_delivered=true` and assume a fetched image means the caller got what they wanted. (Doesn't track whether the URL was ever hit.)
  - C) Rely on MinIO bucket lifecycle only (belt-and-braces per arch §4.2; no application-side reaper).
  - **Recommendation:** A, matches arch §4.2 intent that reaper deletes "completed-but-never-fetched" jobs. Small schema extension.
- **Queue size guard — when does MAX_QUEUE trip?** Currently the handler creates a job row unconditionally, then submits. If we're past MAX_QUEUE we need to reject BEFORE creating the row (otherwise the db fills up with 429'd rows). Decide where the check lives — before `create_queued` or before `queue.put_nowait`?
- **`asyncio.shield` wrapping scope.** Plan says "await the future inside `asyncio.shield`". Is the shield around JUST the wait_for_completion, or around submit+wait+fetch? Arch §4.2 says the worker still finishes even if the client drops — so shield must cover the full adapter sequence. Confirm.
- **`is_disconnected()` cadence.** FastAPI/Starlette's `Request.is_disconnected()` is a poll (O(ms) to check). Poll on every iteration in a loop? Or hook the shield's done callback to check once per phase? Pick one.
- **Where does `response_delivered=true` get committed?** Arch §4.2 says "after the response bytes are on the wire". Starlette/httpx don't have a clean post-flush hook. Options: BackgroundTasks (runs after response returned), or a middleware that intercepts send('http.response.body') with `more_body=false`. Plan recommends the middleware approach.

---

## Environment facts (persistent across sessions)

- **Host:** Windows 11, Docker Desktop, NVIDIA Container Toolkit working.
- **GPU:** RTX 4090 visible in containers (CUDA 13.0, driver 581.80).
- **VRAM:** ~22 GB free before any model load. NoobAI v1.1 uses ~7 GB at inference.
- **ComfyUI sidecar:** `image-gen-comfyui:0.9.2` built and running. NoobAI loaded in VRAM after last generation → next POST is ~1s instead of ~30s.
- **Port conflict:** `free-context-hub-minio-1` uses 9000/9001. Our dev Compose uses `127.0.0.1:9100/9101`.
- **Model files:** `./models/checkpoints/NoobAI-XL-v1.1.safetensors` (6.6 GB) + `./models/vae/sdxl_vae.safetensors` (319 MB).
- **MinIO bucket:** `image-gen` exists. Objects live under `generations/YYYY/MM/DD/<job_id>/<index>.png`.
- **Gateway URL format:** `http://127.0.0.1:8700/v1/images/<job_id>/<index>.png` — Bearer-auth'd stream through our service, NOT S3 presigned.
- **Python:** `.python-version` pins 3.12 for service container. Sidecar uses 3.11 (ComfyUI requirement).
- **Runtime-deps rule** (see `memory/feedback_runtime_correctness.md`): every `import x` in `app/` must be in `[project.dependencies]`. Tests pass under dev venv don't prove the container will boot.
- **Dockerfile `COPY` list:** current image copies `app/` + `migrations/` + entrypoint. When adding a new top-level runtime directory (e.g. Cycle 9's `audit/`), grep the Dockerfile first.

---

## Verify current state before starting next session

```bash
cd d:/Works/source/local-image-generator-service
git status                                        # clean on main
bash scripts/workflow-gate.sh status              # empty
docker compose up -d                              # all 3 services
until docker compose ps --format json comfyui | grep -q '"Health":"healthy"'; do sleep 5; done
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/health | jq .
uv run pytest -q                                  # 158 passed
uv run ruff check .                               # All checks passed
# Live smoke:
curl -s -X POST -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"noobai-xl-v1.1","prompt":"smoke","size":"512x512","steps":1}' \
  http://127.0.0.1:8700/v1/images/generations | jq .
```

If any step fails, read [SESSION.md](SESSION.md) Sprint 6 retro before diving in.

---

## External dependencies

- **LoreWeave integration-guide PR (Cycle 11)** — user-owned, parallel. Soft-blocks Cycle 10 prod acceptance. Not needed for Cycles 4–9 internally.

---

## What NOT to do next session

- Do not start Cycle 5 (LoRAs) before Cycle 4 lands — the sync handler in Cycle 4 becomes the enqueue path that Cycle 5 extends.
- Do not add LoRA fields to `app/validation.py` in Cycle 4 — they stay rejected with `validation_error` until Cycle 5.
- Do not enable `ASYNC_MODE_ENABLED=true` in any config — stays off until Cycle 8. `mode=async` still rejected with `async_not_enabled`.
- Do not re-introduce `BaseHTTPMiddleware` for the new `response_delivered` flusher — pure ASGI only. See [memory/feedback_middleware.md](../../../.claude/projects/d--Works-source-local-image-generator-service/memory/feedback_middleware.md).
- Do not skip the Docker boot test during BUILD — the --no-dev trap bit us twice already. Any new runtime import or new directory requires a live `docker compose up -d image-gen-service` probe before marking VERIFY complete. See [memory/feedback_runtime_correctness.md](../../../.claude/projects/d--Works-source-local-image-generator-service/memory/feedback_runtime_correctness.md).
- Do not rely on `Literal` type annotations for runtime validation. If Cycle 4 adds any Literal-typed config field, pair it with an explicit `_ALLOWED_X = frozenset({...})` check at load time.
