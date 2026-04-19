# Implementation plan — image-gen-service

> **Spec:** [docs/architecture/image-gen-service.md](../architecture/image-gen-service.md) (v0.4)
> **Owner:** @letuhao1994
> **Created:** 2026-04-18
> **Model:** 11 cycles, each going through the 12-phase agentic workflow independently. No MVP compromise — every cycle ships a slice of the full v0.4 spec.

---

## How to read this plan

- **Cycle** = one sprint's worth of work. Roughly one focused workday each, some two. Each cycle is a vertical slice that passes its own end-to-end test before moving on.
- **Cycles are ordered by strict dependency chain** except Cycle 0 (setup) and Cycle 11 (LoreWeave PR) which are parallel.
- **Every cycle lists its own TDD tests + verification command** so "done" is unambiguous.
- **Descope lines** are deliberate scope guards — if you catch yourself doing one of those in a cycle, STOP and defer.
- At the start of each cycle: reset workflow state, classify size (M/L/XL depending on cycle), run the 12 phases.
- At the end of each cycle: tick the global checklist below, update `docs/session/SESSION.md` with a sprint entry.

---

## Global checklist

### Foundation
- [x] **Cycle 0** — Repo bootstrap (pyproject, tooling, CI skeleton, dev compose override) — commit `1943d18`, Sprint 3
- [x] **Cycle 1** — FastAPI skeleton + auth + SQLite job store + structured logging + /health — Sprint 4
- [x] **Cycle 2** — ComfyUI sidecar image + adapter (HTTP + WS) + NoobAI workflow template + anchor resolver — Sprint 5
- [x] **Cycle 3** — MinIO gateway + model registry + first sync `/v1/images/generations` end-to-end — Sprint 6

### Hardening the happy path
- [x] **Cycle 4** — asyncio queue + worker + disconnect handler + orphan reaper + restart recovery — Sprint 7
- [x] **Cycle 5** — LoRA local directory + injection algorithm + vpred injection + path-traversal guard — Sprint 8
- [ ] **Cycle 6** — Civitai fetcher with 11-rule hardening (host allowlist, SHA-256, lock, admin scope, audit)

### Expanding capability
- [ ] **Cycle 7** — Chroma1-HD model #2 (GGUF custom nodes, dual-source anchors, VRAM guard, model unload on swap)
- [ ] **Cycle 8** — Async mode + poll endpoint (feature-flagged)

### Push notifications
- [ ] **Cycle 9** — Webhook dispatcher with full v0.4 hardening (DNS pinning, IP-range, no-redirect, HMAC+ts, multi-secret, TOCTOU, barrier)

### Production readiness
- [ ] **Cycle 10** — Startup validation + smoke test + prod-posture assertions + pre-download script

### Parallel, user-owned
- [ ] **Cycle 11** — LoreWeave integration-guide amendment PR

---

## Cycle overview table

| # | Name | Est | Files (new/mod) | Blocks | Status |
|---|---|---|---|---|---|
| 0 | Repo bootstrap | S→**XL** (actual 14) | 14 | all | [x] `1943d18` |
| 1 | FastAPI + auth + SQLite + logging | M→**XL** (actual 20) | 20 | 2+ | [x] Sprint 4 |
| 2 | ComfyUI sidecar + adapter + NoobAI workflow | L→**XL** (actual 23) | 23 | 3+ | [x] Sprint 5 |
| 3 | MinIO gateway + first sync endpoint | M→**L** (actual 18) | 18 | 4+ | [x] Sprint 6 |
| 4 | Queue + disconnect + reaper + restart recovery | M→**L** (actual 14) | 14 | 8, 9 | [x] Sprint 7 |
| 5 | LoRA local + graph injection | L | 8 | 6 | [ ] |
| 6 | Civitai fetcher hardened | L | 5 | — | [ ] |
| 7 | Chroma model #2 | M | 4 | — | [ ] |
| 8 | Async mode + polling | M | 3 | 9 | [ ] |
| 9 | Webhook dispatcher | XL | 9 | — | [ ] |
| 10 | Startup validation + smoke test | M | 4 | — | [ ] |
| 11 | LoreWeave integration-guide PR | S | 1 (external repo) | — | [ ] |

---

## Cycle 0 — Repo bootstrap

**Goal:** A `git clone && docker compose up` brings up three services (empty image-gen-service returning 200 on `/health`, ComfyUI placeholder, MinIO) with no runtime errors. Tooling (ruff, pytest, pre-commit) enforced locally.

**Size estimate:** S.

**Prerequisites:**
- Docker Desktop + NVIDIA Container Toolkit working on Windows 11 host (confirm before starting).
- RTX 4090 visible to containers via `nvidia-smi` inside a test GPU container.

**Files created:**
- `pyproject.toml` — pinned deps per architecture §14.
- `Dockerfile` — app/image-gen-service base image (Python 3.11, uv/pip install, non-root user).
- `docker-compose.yml` — three services: `image-gen-service`, `comfyui` (placeholder from public image for now), `minio`. Private `internal` network only.
- `docker-compose.override.yml.example` — dev publishes 127.0.0.1:8700 + 127.0.0.1:8188 + 127.0.0.1:9001.
- `.env.example` — documents every env var from arch §5.
- `.pre-commit-config.yaml` — ruff check + format, end-of-file-fixer.
- `tests/__init__.py`, `tests/conftest.py` — pytest setup + httpx AsyncClient fixture.
- `app/main.py` — FastAPI app with `GET /health` returning `{"status":"ok"}` and nothing else.
- `README.md` — quickstart + link to arch doc + link to this plan.

**TDD checks:**
- `pytest tests/test_health.py` — 200 + body shape.
- `ruff check .` clean.
- `docker compose up -d && curl http://127.0.0.1:8700/health` returns 200 from inside the Compose network.

**Descope for this cycle:**
- No auth yet (stub only — tests don't check headers).
- No real ComfyUI sidecar Dockerfile — use public image placeholder, replaced in Cycle 2.
- No CI pipeline config (defer until Cycle 3 when we have something worth CI-ing).

**Verification command:**
```
docker compose up -d && \
curl -sf http://127.0.0.1:8700/health && \
pytest -q && \
ruff check .
```

---

## Cycle 1 — FastAPI skeleton, auth, SQLite, structured logging

**Goal:** Every request through our service is authenticated, every job is persistable, every log line is structured JSON with correlation id. Still no image generation.

**Size estimate:** M.

**Prerequisites:** Cycle 0 complete.

**Files created / modified:**
- `app/auth.py` — multi-key parser (`API_KEYS`, `ADMIN_API_KEYS` comma-separated), `kid` derivation (first 8 chars SHA-256), constant-time compare via `hmac.compare_digest`, FastAPI `Depends` helpers (`require_auth`, `require_admin`).
- `app/middleware/logging.py` — structlog or stdlib JSON logging; correlation id per request.
- `app/queue/store.py` — `aiosqlite` wrapper, migration `001_init.sql` with the jobs table schema from arch §4.2 (including `response_delivered`, `initial_response_delivered`, `webhook_handover`, `webhook_url`, `webhook_headers_json`, `webhook_delivery_status`).
- `app/queue/jobs.py` — `Job` dataclass, CRUD helpers (`create_queued`, `set_running`, `set_completed`, `set_failed`, `get_by_id`).
- `app/api/health.py` — `/health` returns 200 when SQLite reachable, 503 otherwise; auth-gated verbose shape.
- `app/main.py` — register auth middleware, logging middleware, lifespan handler that opens/closes SQLite.
- `tests/test_auth.py` — missing header → 401, wrong key → 401 + `error_code=auth_error`, admin key on generation path ok, generation key on admin path 403.
- `tests/test_job_store.py` — round-trip create/read/update, status transitions validated, concurrent writes don't deadlock.
- `tests/test_health.py` (update) — probes DB, returns 503 when DB path unreadable.
- `config/logging.ini` or equivalent config for JSON output.
- `migrations/` — directory with the SQL files.

**TDD checks:**
- `pytest tests/test_auth.py tests/test_job_store.py tests/test_health.py` — all pass.
- Manual: bring up container, `curl -H 'Authorization: Bearer $KEY' http://127.0.0.1:8700/health` → 200 verbose, without header → 200 boolean only.

**Descope:**
- No `/v1/models`, `/v1/images/generations`, or any real endpoints beyond `/health`.
- No queue worker yet — jobs can be persisted but not executed.

**Verification command:**
```
pytest -q tests/test_auth.py tests/test_job_store.py tests/test_health.py && \
docker compose up -d && \
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/health | jq .
```

---

## Cycle 2 — ComfyUI sidecar + adapter + NoobAI workflow + anchor resolver

**Goal:** Calling our adapter's `generate()` with a hardcoded prompt produces a PNG from real ComfyUI running in a sibling container, using an anchor-tagged workflow template. No HTTP endpoint yet — adapter is directly testable.

**Size estimate:** L. Biggest cycle. Budget 2 days.

**Prerequisites:** Cycle 1 complete. `NoobAI-XL-Vpred-1.0.safetensors` + `sdxl_vae.safetensors` in `./models/`.

**Files created:**
- `docker/comfyui/Dockerfile` — base `nvidia/cuda:12.x-runtime-ubuntu22.04`, ComfyUI pinned to specific tag/commit, `RUN git clone --depth 1 --branch <tag>` for `city96/ComfyUI-GGUF` (even though Chroma isn't in this cycle, pinning now saves rebuild later).
- `docker/comfyui/custom-nodes.txt` — the pin list.
- `docker/comfyui/entrypoint.sh` — launch `python main.py --listen 0.0.0.0 --port 8188`.
- `workflows/sdxl_vpred.json` — anchor-tagged NoobAI workflow. Anchors: `%MODEL_SOURCE%`, `%CLIP_SOURCE%`, `%POSITIVE_PROMPT%`, `%NEGATIVE_PROMPT%`, `%KSAMPLER%`, `%OUTPUT%`. Includes `ModelSamplingDiscrete` for vpred.
- `app/registry/workflows.py` — load JSON, validate required anchors present, find-by-anchor helpers.
- `app/backends/base.py` — `BackendAdapter` Protocol from arch §4.3.
- `app/backends/comfyui.py` — `ComfyUIAdapter` with:
  - `submit(graph, client_id) -> prompt_id`
  - `wait_for_completion(client_id, prompt_id, timeout)` via WebSocket, `/history` polling fallback.
  - `fetch_outputs(prompt_id)` via `/view`, lookup by `%OUTPUT%` anchor.
  - `cancel(prompt_id)` via `/interrupt` + `DELETE /queue`.
  - `free()` via `/free`.
  - `health()` via `/system_stats`.
- `tests/test_anchor_resolver.py` — workflow missing `%MODEL_SOURCE%` fails validation; find-by-anchor returns correct node id.
- `tests/integration/test_comfyui_adapter.py` — **needs real ComfyUI running** (pytest marker `@pytest.mark.integration`). Generates a 1-step 256×256 image, asserts bytes are a valid PNG. Skipped in CI until we have GPU CI.

**TDD checks:**
- Unit: anchor resolver tests.
- Integration: `docker compose up comfyui -d && pytest -m integration tests/integration/test_comfyui_adapter.py` produces a file + verifies PNG magic bytes.
- Manual smoke: exec into ComfyUI container, open web UI on 127.0.0.1:8188, load `workflows/sdxl_vpred.json`, run it, confirm image appears.

**Descope:**
- No LoRA injection (Cycle 5).
- No S3 upload (Cycle 3).
- No HTTP endpoint (Cycle 3).
- No retry logic on adapter calls (add in Cycle 4).
- Chroma workflow is Cycle 7.

**Verification command:**
```
docker compose build comfyui && \
docker compose up -d comfyui && \
pytest -m integration -q tests/integration/test_comfyui_adapter.py
```

---

## Cycle 3 — MinIO upload + first sync `/v1/images/generations` end-to-end

**Goal:** A real HTTP request to `POST /v1/images/generations` with model=noobai returns a JSON body with a signed MinIO URL. The generated PNG is fetchable at that URL for ≥ 1 h. LoreWeave could consume this right now.

**Size estimate:** M.

**Prerequisites:** Cycle 2 complete.

**Files created:**
- `app/storage/s3.py` — two boto3 clients (internal / public endpoint), `upload_png(job_id, index, bytes) -> (bucket, key)`, `presign_get(bucket, key, ttl) -> url` with `tenacity` retry policy (3 attempts, jittered).
- `app/registry/models.py` — load `config/models.yaml`, validate startup (files exist, anchors present, VRAM ≤ budget), hold as a typed `ModelConfig` dataclass.
- `config/models.yaml` — one entry for `noobai-xl-vpred-1` matching arch §4.4 schema.
- `app/api/images.py` — `POST /v1/images/generations` sync path: validate body via Pydantic, resolve model, load workflow, overwrite `%POSITIVE_PROMPT%`/`%NEGATIVE_PROMPT%`/`%KSAMPLER%` fields, call adapter, upload to S3, presign, respond.
- `app/api/models.py` — `GET /v1/models` reading from the registry.
- `app/validation.py` — Pydantic request model enforcing the §6.0 bounds table (minus webhook fields until Cycle 9).
- `tests/test_model_registry.py` — YAML round-trip, missing file fails fast, VRAM-over-budget fails fast.
- `tests/test_sync_endpoint.py` — mocked ComfyUI adapter + mocked S3, asserts response shape. Does NOT exercise real GPU.
- `tests/integration/test_e2e_sync.py` — real ComfyUI + real MinIO. Asserts 200, asserts URL fetchable, asserts content-type `image/png`, asserts image size matches request.

**TDD checks:**
- `pytest tests/test_model_registry.py tests/test_sync_endpoint.py -q`
- `pytest -m integration tests/integration/test_e2e_sync.py` — full pipeline working.

**Descope:**
- No LoRA support on request (rejected at validation if present).
- No async mode (rejected — `ASYNC_MODE_ENABLED=false`).
- No webhook field.
- No queue — sync calls adapter directly in the request handler. (Cycle 4 introduces the queue.)
- No model-swap unload (only one model for now).

**Verification command:**
```
pytest -m "not integration" -q && \
docker compose up -d && \
pytest -m integration -q tests/integration/test_e2e_sync.py && \
curl -X POST -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"noobai-xl-vpred-1","prompt":"test","size":"512x512","steps":8}' \
  http://127.0.0.1:8700/v1/images/generations | jq .
```

---

## Cycle 4 — asyncio queue + disconnect handler + orphan reaper + restart recovery

**Goal:** Sync requests queue behind a single worker; disconnect mid-request doesn't orphan a blob; process restart mid-job produces terminal status for the client.

**Size estimate:** M.

**Prerequisites:** Cycle 3 complete.

**Files created / modified:**
- `app/queue/worker.py` — single asyncio task, pulls Job from `asyncio.Queue`, updates status, drives the adapter, writes result. Queue capacity bounded by `MAX_QUEUE` (arch §12).
- `app/queue/orphan_reaper.py` — background task, TTL-driven, deletes S3 objects of completed-but-never-fetched jobs.
- `app/queue/recovery.py` — on startup, scans SQLite for `running`/`queued` rows; `running` → `failed{service_restarted}`, `queued` → re-enqueue.
- `app/api/images.py` (update) — sync handler enqueues, awaits future inside `asyncio.shield`, watches `Request.is_disconnected()`, on disconnect sets `mode=async, webhook_handover=true` (future Cycle 9 uses this), returns `X-Job-Id` header even on sync. On completion, sets `response_delivered=true` after flush.
- `tests/test_queue_worker.py` — enqueue 3 jobs, verify serial execution, verify `MAX_QUEUE` triggers 429.
- `tests/test_disconnect.py` — simulate client disconnect mid-await; job still reaches `completed`; S3 object exists; reaper cleans after TTL.
- `tests/test_restart_recovery.py` — write `running` row, boot app, assert row flips to `failed{service_restarted}`.

**TDD checks:** each test above.

**Descope:**
- Still no webhook — disconnect flips mode but nothing picks it up yet (Cycle 9 wires the dispatcher).
- No `/interrupt` call on timeout yet — job just times out (add in Cycle 7 during VRAM-guard work).

**Verification command:**
```
pytest -q tests/test_queue_worker.py tests/test_disconnect.py tests/test_restart_recovery.py
```

---

## Cycle 5 — LoRA local directory + graph injection + vpred handling

**Goal:** A request with `loras: [{name, weight}]` produces a visibly different image than the same request without. Path-traversal attempts return 400.

**Size estimate:** L.

**Prerequisites:** Cycle 4 complete. At least one compatible LoRA file placed in `./loras/`.

**Files created:**
- `app/loras/scanner.py` — walk `./loras/`, return `{name, filename, sha256, source, base_model_hint, trigger_words}` pulled from sidecar `<name>.json`.
- `app/api/loras.py` — `GET /v1/loras` (any auth scope).
- `app/registry/workflows.py` (extend) — `inject_loras(graph, loras)` implementing the arch §9 algorithm: find `%MODEL_SOURCE%` / `%CLIP_SOURCE%` anchors, chain `LoraLoader` nodes, rewrite downstream consumers.
- `app/registry/workflows.py` (extend) — `inject_vpred(graph)` inserting `ModelSamplingDiscrete{sampling="v_prediction", zsnr=true}` when `model_cfg.prediction == "vpred"`.
- `app/validation.py` (update) — LoRA name regex `^[A-Za-z0-9_][A-Za-z0-9_\-.]*$`, realpath containment check.
- `tests/test_lora_scanner.py` — directory with 3 loras + 1 sidecar-less → correct output.
- `tests/test_graph_injection.py` — inject 0, 1, 3 LoRAs; assert new node ids chained correctly, downstream references rewritten.
- `tests/test_path_traversal.py` — `loras: [{"name": "../../../etc/passwd"}]` → 400 `error_code=validation_error`.
- `tests/integration/test_lora_effect.py` — integration: generate with vs without LoRA, assert the image hashes differ.

**TDD checks:** all tests above pass.

**Descope:**
- No Civitai fetch (Cycle 6).
- No FLUX/Chroma dual-source anchor handling yet (Cycle 7).
- No LoRA weight auto-normalization — caller supplies exact weight.

**Verification command:**
```
pytest -q tests/test_lora_scanner.py tests/test_graph_injection.py tests/test_path_traversal.py && \
pytest -m integration -q tests/integration/test_lora_effect.py
```

---

## Cycle 6 — Civitai fetcher (11-rule hardening)

**Goal:** Admin-scope client can `POST /v1/loras/fetch` with a Civitai URL, the LoRA lands in `./loras/` with a verified SHA-256, concurrent requests for the same URL dedupe.

**Size estimate:** L.

**Prerequisites:** Cycle 5 complete. `CIVITAI_API_TOKEN` provisioned.

**Files created:**
- `app/loras/civitai.py` — the 11 hardening rules from arch §4.5:
  1. Host allowlist (fixed `civitai.com` + resolved CDN hosts).
  2. `version_id` required.
  3. Metadata fetch with bearer token, download via `files[].primary=true`, `follow_redirects=True`.
  4. SHA-256 verify against `files[].hashes.SHA256`.
  5. `.safetensors` extension only.
  6. `LORA_MAX_SIZE_BYTES` cap.
  7. `LORA_DIR_MAX_SIZE_GB` LRU eviction.
  8. Per-URL `asyncio.Lock` keyed on `(model_id, version_id)`.
  9. Admin scope (already enforced by middleware).
  10. `LORA_MAX_CONCURRENT_FETCHES=1` semaphore.
  11. Audit log line.
- `app/api/loras.py` (update) — add `POST /v1/loras/fetch`.
- `tests/test_civitai_fetch.py` — mocked `httpx.AsyncClient` via `respx`; tests for hash match, hash mismatch, wrong host, wrong extension, size over cap, concurrent fetches serialize.
- `tests/integration/test_civitai_real.py` — OPTIONAL, run against real Civitai with a small public LoRA; gated by env flag. Document in README.

**TDD checks:** all unit tests pass.

**Descope:**
- No background periodic scan (Cycle 6 doesn't auto-refetch).
- No multi-hash (BLAKE3, AutoV2) — deferred per arch §17.
- No sidecar re-verify on use — deferred per arch §17.

**Verification command:**
```
pytest -q tests/test_civitai_fetch.py
```

---

## Cycle 7 — Chroma model #2 + VRAM guard + model unload on swap

**Goal:** A second model (`chroma-hd-q8`) works through the same dispatcher. VRAM budget guard refuses `n*vram_estimate > budget`. Swapping models between requests calls `/free` on ComfyUI.

**Size estimate:** M.

**Prerequisites:** Cycle 6 complete. `chroma1-hd-q8.gguf`, `t5xxl_fp8_e4m3fn.safetensors`, `ae.safetensors`, `clip_l.safetensors` placed in `./models/`.

**Files created / modified:**
- `workflows/chroma_gguf.json` — anchor-tagged Chroma workflow using `UnetLoaderGGUF` + `DualCLIPLoader` + `VAELoader`. `%MODEL_SOURCE%` and `%CLIP_SOURCE%` are different nodes (previously they could overlap for SDXL).
- `app/registry/workflows.py` (extend) — dual-source LoRA injection correctness for FLUX-style graphs.
- `config/models.yaml` (update) — add `chroma-hd-q8` entry with `clip_l`, `t5xxl`, `dual_clip_type: chroma`, `prediction` absent (FLUX isn't vpred).
- `app/backends/comfyui.py` (extend) — `unload_models()` calls `/free {unload_models: true, free_memory: true}`, probes `/system_stats` until VRAM drops.
- `app/queue/worker.py` (extend) — track `last_model_name`, call `unload_models()` when next job differs.
- `app/validation.py` (extend) — VRAM guard `model_cfg.vram_estimate_gb + lora_overhead <= VRAM_BUDGET_GB`, `error_code=vram_budget_exceeded`.
- `tests/test_vram_guard.py` — mock registry with 15 GB model in 12 GB budget → 400.
- `tests/test_dual_source_injection.py` — graph with separate UNET + DualCLIP nodes + 2 LoRAs injected.
- `tests/integration/test_model_swap.py` — generate with NoobAI, generate with Chroma, assert VRAM freed between (via `/system_stats` probe).

**TDD checks:** all tests above.

**Descope:**
- No automatic model prewarming.
- No multi-model concurrent inference (single worker).

**Verification command:**
```
pytest -q tests/test_vram_guard.py tests/test_dual_source_injection.py && \
pytest -m integration -q tests/integration/test_model_swap.py
```

---

## Cycle 8 — Async mode + polling endpoint

**Goal:** With `ASYNC_MODE_ENABLED=true`, a `POST` with `mode=async` returns 202 + `{id, status: processing}`; `GET /v1/images/generations/{id}` reflects queue→running→completed; pure-async clients never block.

**Size estimate:** M.

**Prerequisites:** Cycle 7 complete. Feature flag off by default.

**Files created / modified:**
- `app/api/images.py` (extend) — async branch when `mode=async` and flag on; set `initial_response_delivered=true` after 202 flushes; return job id.
- `app/api/images.py` — new `GET /v1/images/generations/{id}` with §6.3 response shape (including `webhook_delivery_status` which stays null for now).
- `app/validation.py` (extend) — reject `mode=async` with `async_not_enabled` when flag off.
- `tests/test_async_mode.py` — flag off → 400; flag on → 202, poll converges to completed, result matches what sync would return for same input.

**TDD checks:** tests above pass.

**Descope:**
- No webhook yet (Cycle 9).
- Async flag stays off in prod `.env.example` — it'll flip when LoreWeave's adapter is confirmed to handle 202+poll (gated by Cycle 11).

**Verification command:**
```
pytest -q tests/test_async_mode.py
```

---

## Cycle 9 — Webhook dispatcher (full v0.4 hardening)

**Goal:** A request with `webhook: {url, headers}` in async mode results in a correctly-signed POST to the URL on terminal transition. All 14 `/review-impl` findings resolved in code. Fake-receiver test proves HMAC + timestamp + dedupe work end-to-end.

**Size estimate:** XL. Probably two days. Subagent dispatch is worth considering for component slicing.

**Prerequisites:** Cycle 8 complete. `WEBHOOK_SIGNING_SECRETS` set. `IMAGEGEN_ENV=dev` with `WEBHOOK_ALLOW_ANY_HOST=true` for tests; `IMAGEGEN_ENV=prod` only after LoreWeave's receiver is live (Cycle 11).

**Files created:**
- `app/webhooks/signing.py` — `sign(ts, body, secret) -> hex`, `build_header(ts, hex) -> str`, constant-time verify helper (for our own tests to use).
- `app/webhooks/dispatcher.py` — separate asyncio task, enqueue-from-SQLite on terminal transitions, per-attempt delivery loop, retry schedule, TOCTOU re-validation per attempt, `WEBHOOK_MAX_IN_FLIGHT` semaphore.
- `app/webhooks/dns.py` — resolve-once-at-dispatch, IP range check (RFC1918 / loopback / link-local / ULA), connect-by-IP with explicit Host header via custom httpx transport.
- `app/webhooks/retry.py` — 5-attempt schedule `[15, 60, 300, 900, 3600]` seconds + jitter helper.
- `app/queue/store.py` (extend) — `webhook_deliveries` table + migration `002_webhook_deliveries.sql`.
- `app/api/images.py` (extend) — wire `webhook_handover` barrier: sync handler writes it; async handler writes it on 202 flush.
- `app/api/admin.py` (extend) — `GET /v1/webhooks/deliveries/{job_id}`.
- `app/validation.py` (extend) — `webhook.url` + `webhook.headers` bounds per arch §6.0, reserved-header rejection, `IMAGEGEN_ENV`-dependent scheme check, allowlist check.
- `tests/test_signing.py` — known-vector HMAC, constant-time compare, Stripe-style `t=…,v1=…` header parsing.
- `tests/test_dns_pinning.py` — mock `getaddrinfo`; assert private IP rejected, public IP accepted, httpx transport uses IP not hostname.
- `tests/test_redirect.py` — mock receiver returns 302 → dispatcher marks `webhook_redirect` terminal, no retry.
- `tests/test_toctou_revalidation.py` — allowlist tightened between attempts → next attempt blocks with `webhook_ssrf_blocked`.
- `tests/test_barrier.py` — sync-mode race: worker completes before handler flushes, dispatcher holds until `webhook_handover=true`, then runs and suppresses because `response_delivered=true`.
- `tests/test_secret_rotation.py` — dispatcher signs with first secret; new secret prepended mid-retry; in-flight delivery uses the current-first-secret at the time of *each* attempt.
- `tests/test_error_after_completion.py` — presign fails after `status=completed` → job flips to `failed{storage_error}` before response flush → webhook fires `job.failed`, consistent with HTTP response.
- `tests/integration/test_webhook_e2e.py` — fake receiver (tiny uvicorn app on localhost with `WEBHOOK_ALLOW_PRIVATE=true` dev) that implements the Go-equivalent verification in Python; submits async job + webhook, asserts fake receiver got exactly-one 2xx-responding call with valid signature.

**TDD checks:** every test above passes, including integration.

**Descope:**
- No webhook for `mode=sync` + success — that's the suppression case (tests cover it).
- No progress (`running`) events — terminal only per arch.
- `WEBHOOK_ALLOWED_HOSTS` is still set by operator; we do not hardcode LoreWeave's host in the image.

**Verification command:**
```
pytest -q tests/test_signing.py tests/test_dns_pinning.py tests/test_redirect.py \
         tests/test_toctou_revalidation.py tests/test_barrier.py \
         tests/test_secret_rotation.py tests/test_error_after_completion.py && \
pytest -m integration -q tests/integration/test_webhook_e2e.py
```

---

## Cycle 10 — Startup validation + smoke test + prod posture + pre-download

**Goal:** `docker compose up` with `IMAGEGEN_ENV=prod` refuses to boot on any misconfiguration (empty allowlist, `WEBHOOK_ALLOW_ANY_HOST=true`, empty signing secrets, missing checkpoint, missing anchors). Valid prod config passes all 11 startup steps and serves traffic within 2 minutes of boot.

**Size estimate:** M.

**Prerequisites:** Cycle 9 complete.

**Files created / modified:**
- `app/main.py` (extend) — `lifespan` handler performs all 11 startup steps from arch §16 in order, refuses to boot on any failure with structured `startup_failed{stage, reason}` log + non-zero exit.
- `app/startup/checks.py` — one function per step, unit-testable.
- `app/startup/smoke_test.py` — for each registered model, submit a 1-step 256×256 prompt with fixed seed, wait up to 120 s, confirm completion.
- `scripts/pull-models.sh` — `huggingface-cli` wrapper reading from `config/models.yaml`, retries on rate-limit, prints disk usage delta.
- `tests/test_startup_checks.py` — each check unit-tested: missing file fails, public-IP ComfyUI fails, empty allowlist in prod fails, `WEBHOOK_ALLOW_ANY_HOST` in prod fails, workflow missing anchor fails.
- `tests/integration/test_smoke_boot.py` — bring up prod-mode Compose, assert service serves `/health` within 120 s; tear down and restart with deliberately broken config, assert exit code non-zero and `startup_failed` log line present.

**TDD checks:** every test above.

**Descope:**
- No GitHub Actions CI — we've kept it informal. Optional follow-up if you want to formalize.

**Verification command:**
```
pytest -q tests/test_startup_checks.py && \
IMAGEGEN_ENV=prod WEBHOOK_ALLOWED_HOSTS=example.com WEBHOOK_SIGNING_SECRETS=abc \
  docker compose up -d && \
curl -sf http://127.0.0.1:8700/health
```

---

## Cycle 11 — LoreWeave integration-guide amendment PR (parallel, user-owned)

**Goal:** Land a PR in the LoreWeave repo adding the async + webhook contract so LoreWeave's OpenAI-fallback adapter handles our 202 response and a `POST /v1/webhooks/image-gen` receiver verifies our deliveries.

**Size estimate:** S (from this repo's perspective — the work is elsewhere).

**Prerequisites:** None from this repo; can be done in parallel with any cycle. Ideally land before Cycle 9 ships so we have a real receiver to integration-test against.

**Deliverables (in LoreWeave repo, owned by @letuhao1994):**
- Amendment to `docs/EXTERNAL_AI_SERVICE_INTEGRATION_GUIDE.md` §6, adding:
  - Async mode contract (`mode=async`, 202, poll).
  - Webhook request field + receiver contract.
  - Literal Go verification snippet from our arch §10.
  - At-least-once + durable-dedupe requirement.
- Adapter in `services/provider-registry-service/internal/provider/` that:
  - Supports async invocation (submit + poll until terminal).
  - Optionally accepts a webhook URL from the caller, relays to LoreWeave's receiver.
- Receiver route `POST /v1/webhooks/image-gen` that:
  - Reads raw body.
  - Verifies HMAC-SHA256 with rotating secret set via `hmac.Equal`.
  - Checks `|now - ts| <= 300`.
  - Dedupes by `X-ImageGen-Job-Id` using a SQL table.
  - Commits the job result.
  - Responds 2xx within 10 s.

**Helper deliverable in THIS repo:**
- `docs/integration/lore-weave-receiver-reference.md` — copy of the Go snippet from arch §10 with an introductory paragraph. Give @letuhao1994 something to paste/link in the LoreWeave PR.

**Verification:**
- LoreWeave PR merged.
- LoreWeave test env's receiver receives a signed delivery from our dev container and responds 2xx.
- End-to-end: LoreWeave submits async request → our service generates → webhook lands → LoreWeave's UI shows completed generation.

**Descope:**
- Not responsible for LoreWeave's UI changes — only the receiver endpoint + adapter.

---

## Dependency graph

```
  Cycle 0 ────┐
              ▼
  Cycle 1 ────┐
              ▼
  Cycle 2 ────┐
              ▼
  Cycle 3 ────┬─────────────┬──────────────┐
              ▼             ▼              ▼
  Cycle 4   Cycle 5       Cycle 6        Cycle 7
              │             │              │
              ▼             ▼              │
             (feeds into)                  │
                                           ▼
                                      Cycle 8
                                           │
                                           ▼
                                      Cycle 9 ◀── Cycle 11 (LoreWeave PR)
                                           │               (soft blocker
                                           ▼                for prod)
                                      Cycle 10
```

Cycle 11 runs in parallel but soft-blocks Cycle 10's prod-mode acceptance test (can't prove end-to-end to LoreWeave without LoreWeave's receiver).

---

## Open items to resolve before each cycle starts

Resolve these in the cycle's CLARIFY phase so they don't stall BUILD:

| Cycle | Unknown | Resolution owner | When |
|---|---|---|---|
| 0 | ~~Docker Desktop + NVIDIA Container Toolkit working on Win11 host?~~ | ~~@letuhao1994~~ | ~~before Cycle 0 CLARIFY~~ — **resolved Sprint 3**: GPU passthrough works, RTX 4090 + CUDA 13 in container |
| 2 | Exact ComfyUI tag/commit to pin; exact `city96/ComfyUI-GGUF` commit | me + @letuhao1994 | during Cycle 2 CLARIFY |
| 3 | LoreWeave's HTTP client timeout (risk #1 from pre-BUILD concerns) | @letuhao1994 | before Cycle 3 |
| 5 | Which Civitai LoRA to use for visible-effect integration test | @letuhao1994 | during Cycle 5 CLARIFY |
| 7 | Exact HF repo paths for Chroma Q8 + T5 + VAE | me | before Cycle 7 |
| 9 | Webhook secret format (hex vs base64 vs raw) | me | during Cycle 9 CLARIFY; default hex |
| 10 | Prod-mode integration test target (local prod-mode compose OK?) | @letuhao1994 | during Cycle 10 CLARIFY |
| 11 | LoreWeave PR review timeline | @letuhao1994 | at repo start |

---

## Retro touch-points per cycle

After each cycle, add to `docs/session/SESSION.md`:
- One-line outcome.
- Files changed.
- Review findings and how fixed.
- Integration-test evidence.
- What's next (feeds the next cycle's CLARIFY).

Retro lessons only when non-obvious — don't narrate happy-path completions.

---

*End of implementation plan.*
