# Handoff — next session start here

> This file is **overwritten** every session close. It reflects **current state**, not history.
> History lives in [SESSION.md](SESSION.md). Architecture lives in [docs/architecture/image-gen-service.md](../architecture/image-gen-service.md). Build plan lives in [docs/plans/2026-04-18-image-gen-service-build.md](../plans/2026-04-18-image-gen-service-build.md).

**Last updated:** 2026-04-19 — Session closed after Sprint 5 / Cycle 2.

---

## Where we are

- **Branch:** `main`, **3 commits ahead** of `origin/main` (not pushed).
- **Commits since origin:**
  - `9b13ea9` feat(cycle-1): FastAPI auth + SQLite job store + structured JSON logging
  - `08e67aa` docs: session close — rewrite HANDOFF.md for Cycle 2 pickup
  - `2ff43a7` feat(cycle-2): ComfyUI sidecar + BackendAdapter + anchor-tagged NoobAI workflow
- **Plan progress:** 3 / 11 cycles complete.

```
[x] 0  Repo bootstrap
[x] 1  FastAPI + auth + SQLite + logging
[x] 2  ComfyUI sidecar + adapter + NoobAI workflow
[ ] 3  MinIO + first sync endpoint                 ← NEXT (M, 1-day budget)
[ ] 4  Queue + disconnect + reaper + restart
[ ] 5  LoRA local + injection
[ ] 6  Civitai fetcher hardened
[ ] 7  Chroma model #2
[ ] 8  Async + polling
[ ] 9  Webhook dispatcher
[ ] 10 Startup validation + smoke test
[ ] 11 LoreWeave integration-guide PR (parallel, user-owned)
```

- **Workflow state:** clean (last task `retro` still pending close — will close with the session-close commit).
- **Test suite:** `uv run pytest -q` → 87 passed (85 unit + 2 real-GPU integration). `uv run ruff check .` / `ruff format --check .` clean.
- **Arch version:** v0.5 (v1.1 eps model + unified `./models/` tree).

---

## Next action (Sprint 6 = Cycle 3)

**Goal per plan §Cycle 3:** `POST /v1/images/generations` with `model=noobai-xl-v1.1` returns a JSON body with a signed MinIO URL. The PNG is fetchable at that URL for ≥ 1 h. LoreWeave could consume this right now.

Files to create:

- `app/storage/__init__.py`, `app/storage/s3.py` — two boto3 clients (internal endpoint `http://minio:9000` for upload; public endpoint for presign), `upload_png(job_id, index, bytes) -> (bucket, key)`, `presign_get(bucket, key, ttl) -> url` wrapped in `tenacity` retry (3 attempts, jittered).
- `app/registry/models.py` — `load_registry(path) -> Registry`, startup validation (files exist under `models/`, workflow JSON has required anchors via `validate_anchors`, `vram_estimate_gb ≤ VRAM_BUDGET_GB`). `ModelRegistry` dataclass, `get(name)` lookup.
- `config/models.yaml` — one entry for `noobai-xl-v1.1` matching arch v0.5 §4.4 (checkpoint: `checkpoints/NoobAI-XL-v1.1.safetensors`, vae: `vae/sdxl_vae.safetensors`, prediction: `eps`, workflow: `workflows/sdxl_eps.json`, limits + defaults).
- `app/api/images.py` — `POST /v1/images/generations` sync path: Pydantic validate → registry lookup → `load_workflow` + copy.deepcopy → overwrite `%POSITIVE_PROMPT%` / `%NEGATIVE_PROMPT%` / `%KSAMPLER%` fields via anchor lookup → `adapter.submit` → `wait_for_completion` → `fetch_outputs` → `upload_png` per image → `presign_get` → respond.
- `app/api/models.py` — `GET /v1/models` reading from the registry, OpenAI-compatible shape plus `capabilities` + `backend`.
- `app/validation.py` — Pydantic `GenerateRequest` enforcing arch §6.0 bounds: `prompt` 1..8000, `negative_prompt` 0..2000, `size` regex + model.limits.size_max_pixels, `n` 1..n_max, `steps` 1..steps_max, `cfg` 0..30, `seed` ≥ -1, `sampler`/`scheduler` enums, `response_format` enum. **Reject webhook fields** in Cycle 3 (added in Cycle 9).
- `tests/test_model_registry.py` — YAML round-trip, missing-file fails fast, workflow-missing-anchor fails fast, VRAM-over-budget fails fast.
- `tests/test_validation.py` — each bound enforced, illegal values raise 400.
- `tests/test_sync_endpoint.py` — mocked `ComfyUIAdapter` + mocked `S3Storage`, asserts response shape + X-Job-Id header.
- `tests/integration/test_e2e_sync.py` — real ComfyUI + real MinIO, asserts 200, URL fetchable, content-type `image/png`, image size matches request.

Also modify:

- `app/main.py` — add adapter + registry + s3 to `app.state` in lifespan; mount `images_router` + `models_router`.
- `pyproject.toml` — add `boto3>=1.35,<2`, `tenacity>=9`.
- `.env.example` — `S3_BUCKET=image-gen` is already there; confirm `S3_INTERNAL_ENDPOINT` / `S3_PUBLIC_ENDPOINT` too.
- `docker-compose.yml` — MinIO bucket init (entrypoint or `mc` sidecar).

**Kickoff commands:**
```bash
cd d:/Works/source/local-image-generator-service
bash scripts/workflow-gate.sh reset
# Plan says Cycle 3 is M (~6 files). Script enforces honesty if counts drift.
bash scripts/workflow-gate.sh size M 6 5 1
bash scripts/workflow-gate.sh phase clarify
```

---

## Open items to resolve during Cycle 3 CLARIFY

- **LoreWeave HTTP client timeout on sync path** — flagged in plan's unknowns table (line 547) as owner @letuhao1994. Drives: the `size_max_pixels` ceiling (smaller images → faster generation → less risk of LoreWeave's client timing out), and whether Cycle 4 can safely keep the queue synchronous under disconnect-recovery. Need a number before finalizing the Pydantic request bounds.
- **MinIO bucket creation strategy** — the `image-gen` bucket does not exist on first boot. Three options: (A) add a `mc` init sidecar to docker-compose that creates the bucket, (B) have the service's lifespan create it on startup (idempotent via `head_bucket` + `create_bucket`), (C) document as a manual step in README. Recommendation: B (no new service, fail-fast if MinIO down).
- **Presign URL TTL default** — arch §5 says `PRESIGN_TTL_S=3600` (1 h). Matches arch §4.2 orphan reaper TTL (24 h) comfortably. Confirm and set in `.env.example`.
- **Public vs internal S3 endpoint for presign** — dev has both on the same host (`http://127.0.0.1:9100`). Prod (Novita) will have internal (`http://minio-svc:9000`) and public (`https://images.example.com`). The presign must use the public endpoint; upload uses internal. Spec §11.1 says two clients. Confirm that boto3 instance accepts different endpoint URLs.
- **Empty-output handling** — if ComfyUI returns zero images (shouldn't happen in practice but), should we 200 with `data: []` or 500? Arch §13 has `internal` as the catch-all. Recommendation: 500 with `error_code=internal`.

---

## Environment facts (persistent across sessions)

- **Host:** Windows 11, Docker Desktop, NVIDIA Container Toolkit working.
- **GPU:** RTX 4090 visible in containers, CUDA 13.0, driver 581.80, **22.3 / 24 GB VRAM free** (the ~17 GB previously in use was freed between sessions).
- **ComfyUI sidecar:** `image-gen-comfyui:0.9.2` built and running. First build is 5-10 min; rebuilds (no pin bump) reuse cached layers (~30 s).
- **Port conflict:** `free-context-hub-minio-1` uses 9000/9001. Our dev Compose uses **127.0.0.1:9100/9101** for MinIO. Internal container port stays 9000.
- **Python:** `.python-version` pins 3.12 for the service container. Sidecar uses Python 3.11 (ComfyUI's hard requirement).
- **ComfyUI quirks:** See memory file `reference_comfyui_quirks.md` — `ckpt_name` must NOT include `checkpoints/` prefix; GGUF folder hyphen blocks direct imports; `status.completed=True` means terminal (success OR error) — check `status_str`; 400+node_errors is `ComfyNodeError` not `ComfyUnreachableError`.
- **Arch v0.5 deferred item:** `./loras/` mount was removed from comfyui service in Cycle 2. Cycle 5 CLARIFY must decide: put LoRAs in `./models/loras/` (rename `/loras` mount on image-gen-service to `/models/loras`), or add `./loras:/workspace/ComfyUI/models/loras:ro` back on comfyui. Noted in [arch §20 v0.5](../architecture/image-gen-service.md).

---

## Verify current state before starting next session

```bash
cd d:/Works/source/local-image-generator-service
git status                                        # should be clean on main
bash scripts/workflow-gate.sh status              # should be empty
docker compose up -d                              # bring stack back up (all 3 services)
until docker compose ps --format json comfyui | grep -q '"Health":"healthy"'; do sleep 5; done
curl -sf http://127.0.0.1:8700/health             # → {"status":"ok"}
curl -sf http://127.0.0.1:8188/system_stats > /dev/null && echo "comfyui ok"
uv run pytest -q                                  # → 87 passed
uv run ruff check .                               # → All checks passed
```

If any of the above fails, read [SESSION.md](SESSION.md) Sprint 5 retro before diving in.

---

## External dependencies

- **LoreWeave integration-guide PR (Cycle 11)** — user-owned, parallel. Soft-blocks Cycle 10 prod acceptance. Not needed for Cycles 3–9 internally. Draft before Cycle 9.
- **LoreWeave HTTP client timeout** — needed for Cycle 3 CLARIFY (see open items above).

---

## What NOT to do next session

- Do not start Cycle 4 (queue) before Cycle 3 lands — Cycle 4's worker calls `app.state.adapter` + `app.state.store` wired up in Cycle 3's lifespan changes.
- Do not put LoRA-related fields in `app/validation.py` — LoRAs are Cycle 5. If a request includes `loras`, reject with `validation_error`.
- Do not implement async mode — `ASYNC_MODE_ENABLED=false` stays false. Reject `mode=async` with `async_not_enabled` per arch §6.2.
- Do not introduce retries on ComfyUI calls at the API layer — adapter retry policy is Cycle 4 (alongside the queue). Adapter already has one-WS-reconnect-then-poll; don't wrap it a second time.
- Do not use httpx `BaseHTTPMiddleware` anywhere (still). Pure ASGI middleware only. See [memory/feedback_middleware.md](../../../.claude/projects/d--Works-source-local-image-generator-service/memory/feedback_middleware.md).
- Do not assume ComfyUI's `/history[pid].status.completed==True` means success — it's set on failure too. Always check `status_str` (or use `app.backends.comfyui._raise_if_errored`).
