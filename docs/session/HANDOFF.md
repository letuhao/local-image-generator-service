# Handoff — next session start here

> This file is **overwritten** every session close. It reflects **current state**, not history.
> History lives in [SESSION.md](SESSION.md). Architecture lives in [docs/architecture/image-gen-service.md](../architecture/image-gen-service.md). Build plan lives in [docs/plans/2026-04-18-image-gen-service-build.md](../plans/2026-04-18-image-gen-service-build.md).

**Last updated:** 2026-04-19 — Session closed after Sprint 4 / Cycle 1.

---

## Where we are

- **Branch:** `main`, **1 commit ahead** of `origin/main` (not pushed).
- **Commit since origin:**
  - `9b13ea9` feat(cycle-1): FastAPI auth + SQLite job store + structured JSON logging
- **Plan progress:** 2 / 11 cycles complete.

```
[x] 0  Repo bootstrap
[x] 1  FastAPI + auth + SQLite + logging
[ ] 2  ComfyUI sidecar + adapter + NoobAI workflow          ← NEXT (L, 2-day budget)
[ ] 3  MinIO + first sync endpoint
[ ] 4  Queue + disconnect + reaper + restart
[ ] 5  LoRA local + injection
[ ] 6  Civitai fetcher hardened
[ ] 7  Chroma model #2
[ ] 8  Async + polling
[ ] 9  Webhook dispatcher
[ ] 10 Startup validation + smoke test
[ ] 11 LoreWeave integration-guide PR (parallel, user-owned)
```

- **Workflow state:** clean (last task `retro` completed). Reset ready for next cycle.
- **Test suite:** `uv run pytest -q` → 53 passed. `uv run ruff check .` / `ruff format --check .` clean.

---

## Next action (Sprint 5 = Cycle 2)

**Goal per plan §Cycle 2:** Calling the adapter's `generate()` with a hardcoded prompt produces a PNG from real ComfyUI running in a sibling container, using an anchor-tagged workflow template. **No HTTP endpoint yet** — adapter is directly testable.

Files to create:

- `docker/comfyui/Dockerfile` — base `nvidia/cuda:12.x-runtime-ubuntu22.04`, ComfyUI pinned to a specific tag/commit, `RUN git clone --depth 1 --branch <tag>` for `city96/ComfyUI-GGUF` (pin now even though Chroma is Cycle 7).
- `docker/comfyui/custom-nodes.txt` — pin list for the custom nodes.
- `docker/comfyui/entrypoint.sh` — launch `python main.py --listen 0.0.0.0 --port 8188`.
- `workflows/sdxl_vpred.json` — anchor-tagged NoobAI workflow. Anchors: `%MODEL_SOURCE%`, `%CLIP_SOURCE%`, `%POSITIVE_PROMPT%`, `%NEGATIVE_PROMPT%`, `%KSAMPLER%`, `%OUTPUT%`. Include `ModelSamplingDiscrete` for vpred.
- `app/registry/__init__.py`, `app/registry/workflows.py` — load JSON, validate required anchors present, find-by-anchor helpers.
- `app/backends/__init__.py`, `app/backends/base.py` — `BackendAdapter` Protocol from arch §4.3.
- `app/backends/comfyui.py` — `ComfyUIAdapter` (submit via `POST /prompt`, wait via `ws://.../ws?clientId=...`, poll `/history/{prompt_id}` fallback, fetch via `/view`, cancel via `/interrupt` + `DELETE /queue`, `/free`, `/system_stats`).
- `tests/test_anchor_resolver.py` — missing anchor fails validation; find-by-anchor returns correct node id.
- `tests/integration/__init__.py`, `tests/integration/test_comfyui_adapter.py` — marked `@pytest.mark.integration`, needs real GPU, generates a 1-step 256×256 PNG, asserts PNG magic bytes.

Replace in `docker-compose.yml`: swap the `image: nginx:alpine` placeholder comfyui with `build: ./docker/comfyui` + GPU reservation per arch §5.

Add deps: `httpx`, `websockets` (Cycle 9 plan mentions `websockets>=13,<14`).

**Kickoff commands:**
```bash
cd d:/Works/source/local-image-generator-service
bash scripts/workflow-gate.sh reset
# Plan calls Cycle 2 size L (10 files). Script should agree this time.
bash scripts/workflow-gate.sh size L 10 6 1   # files, logic, side_effects
bash scripts/workflow-gate.sh phase clarify
```

---

## Open items to resolve during Cycle 2 CLARIFY

- **ComfyUI tag/commit to pin.** Plan's unknowns table (line 546) flags this as jointly owned with user. Need: a tag that still exists on GitHub, is known-stable with CUDA 13.0, and is compatible with `city96/ComfyUI-GGUF`.
- **`city96/ComfyUI-GGUF` commit to pin.** Same table. Find a commit that matches the ComfyUI tag above.
- **`NoobAI-XL-Vpred-1.0.safetensors` + `sdxl_vae.safetensors` in `./models/` on the host** — user must place these before integration test can run (required by Cycle 2 prereq).
- **ComfyUI WebSocket reconnect policy.** Arch §4.3 says "fall back to polling `/history/` every `COMFY_POLL_INTERVAL_MS` if WS disconnects, capped by `JOB_TIMEOUT_S`". Decide: reconnect WS once, or go straight to polling? Plan says fallback to polling.
- **`client_id` scope.** Arch §4.3 says "per-adapter-instance"; test semantics want per-job filtering. Decide in CLARIFY: single WS connection per adapter (one `client_id`, filter prompt_id on each WS event), or per-job WS connection (fresh `client_id` per submit). Single-worker architecture → single-connection works; document in design.

---

## Environment facts (persistent across sessions)

- **Host:** Windows 11, Docker Desktop, NVIDIA Container Toolkit working (verified Sprint 3).
- **GPU:** RTX 4090 visible in containers (CUDA 13.0, driver 581.80).
- **VRAM pressure:** 17.2 / 24 GB already in use on the host before any model load. Chroma Q8's ~9 GB will exceed the 12 GB budget → something must be freed before Cycle 7.
- **Port conflict:** `free-context-hub-minio-1` uses 9000/9001. Our dev Compose uses **127.0.0.1:9100/9101** for MinIO. Internal container port stays 9000.
- **Python:** `.python-version` pins 3.12 for Docker; host can be 3.13 (pyproject allows `<3.14`).
- **uv:** 0.9.11 installed on host. Dockerfile pins 0.9.11 too.
- **pyenv-win quirk:** `python3` shim mangles multi-line `-c` on Windows. `scripts/workflow-gate.sh` works around it by preferring `python`.
- **SQLite:** `/app/data/jobs.db` inside container, bind-mounted from `./data/` on host. WAL mode enforced. Migration runner applies `migrations/NNN_*.sql` on every lifespan entry, idempotent.
- **Auth:** `API_KEYS` + `ADMIN_API_KEYS` read from env at lifespan start. Empty keysets → service boots, every auth-required request 401s (fail-closed). `/health` boolean shape works without auth.

---

## Verify current state before starting next session

```bash
cd d:/Works/source/local-image-generator-service
git status                                  # should be clean on main
bash scripts/workflow-gate.sh status        # should be empty / last retro complete
uv run pytest -q                            # → 53 passed
uv run ruff check .                         # → All checks passed
docker compose up -d                        # bring stack back up (comfyui placeholder + minio)
curl -sf http://127.0.0.1:8700/health       # → {"status":"ok"}
```

If any of the above fails, read [SESSION.md](SESSION.md) Sprint 4 retro before diving in.

---

## External dependencies blocking no cycles yet

- **LoreWeave integration-guide PR (Cycle 11)** — user-owned, parallel. Soft-blocks Cycle 10 prod acceptance test. Not needed for Cycles 1–9 internally. Should draft before Cycle 9 integration tests.
- **Model files** — Cycle 2 BUILD cannot run integration test until `NoobAI-XL-Vpred-1.0.safetensors` + `sdxl_vae.safetensors` are in `./models/` on the host.

---

## What NOT to do next session

- Do not start Cycle 3 (MinIO + sync endpoint) before Cycle 2 lands — Cycle 3's sync endpoint calls the adapter directly.
- Do not replace the `image: nginx:alpine` placeholder comfyui until you have both the Dockerfile AND the workflow AND the adapter ready — otherwise `docker compose up` breaks for anyone who pulls mid-cycle.
- Do not skip the workflow phases "because we've already designed it" — Cycle 2 CLARIFY still needs to resolve the 5 open items above, especially the tag pins.
- Do not widen redaction patterns in `app/logging_config.py` casually — the regexes there are conservative on purpose (rather over-redact than leak). Cycle 1 /review-impl MED-4 covers the rationale.
- Do not re-introduce `BaseHTTPMiddleware` for new middleware — it breaks FastAPI's exception-handler chain. Pure ASGI only (see the `RequestContextMiddleware` pattern in `app/middleware/logging.py`).
