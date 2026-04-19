# Session log

> Append the newest sprint at the top. Keep each entry short: one-line outcome, changed files, notable decisions, what's next.

**Last session ended:** 2026-04-19 after Sprint 5 / Cycle 2 complete. Resume from [HANDOFF.md](HANDOFF.md) — it holds the pick-up-where-you-left-off summary.

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

