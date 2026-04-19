# Handoff — next session start here

> This file is **overwritten** every session close. It reflects **current state**, not history.
> History lives in [SESSION.md](SESSION.md). Architecture lives in [docs/architecture/image-gen-service.md](../architecture/image-gen-service.md). Build plan lives in [docs/plans/2026-04-18-image-gen-service-build.md](../plans/2026-04-18-image-gen-service-build.md).

**Last updated:** 2026-04-19 — Session closed after Sprint 9 / Cycle 6.

---

## Where we are

- **Branch:** `main`, ahead of `origin/main` by whatever Cycle 6 will add. Check `git log origin/main..HEAD`.
- **Latest commits (most-recent first):**
  - `<cycle6>` feat(cycle-6): Civitai fetcher — URL parser, async queue, SHA verify, LRU eviction, /review-impl fixes
  - `bc3edac` fix(compose): make ComfyUI user tree writable + drop workflows ro overlay
  - `d74222b` docs: fix HANDOFF commit-count to reflect actual origin/main state
  - `af615a5` feat(cycle-5): LoRA scanner + graph injection + GET /v1/loras + path-traversal defense
- **Plan progress:** 7 / 11 cycles complete.

```
[x] 0  Repo bootstrap
[x] 1  FastAPI + auth + SQLite + logging
[x] 2  ComfyUI sidecar + adapter + NoobAI workflow
[x] 3  MinIO gateway + model registry + first sync endpoint
[x] 4  Queue + disconnect + reaper + restart
[x] 5  LoRA local + injection
[x] 6  Civitai fetcher hardened
[ ] 7  Chroma model #2 + VRAM guard + model unload on swap  ← NEXT (M, 1 day budget)
[ ] 8  Async + polling
[ ] 9  Webhook dispatcher
[ ] 10 Startup validation + smoke test
[ ] 11 LoreWeave integration-guide PR (parallel, user-owned)
```

- **Workflow state:** RETRO pending close (will close with the Cycle 6 commit).
- **Test suite:** `uv run pytest --ignore=tests/integration -q` → **281 passed / 2 skipped** (Windows symlink gates). Integration: `uv run pytest -m integration -q tests/integration/` gated on `CIVITAI_API_TOKEN` + `CIVITAI_TEST_URL`.
- **Arch version:** v0.6 (unchanged). Cycle 6 needed no arch revision.

---

## Next action (Sprint 10 = Cycle 7)

**Goal per plan §Cycle 7:** A second model (`chroma-hd-q8`) works through the same dispatcher. VRAM budget guard refuses requests where `n * vram_estimate_gb + overhead > VRAM_BUDGET_GB`. Swapping models between requests calls `/free` on ComfyUI to release VRAM before the new load.

**Files to create / modify:**

- `workflows/chroma_gguf.json` — anchor-tagged Chroma workflow using `UnetLoaderGGUF` + `DualCLIPLoader` + `VAELoader`. `%MODEL_SOURCE%` and `%CLIP_SOURCE%` land on DIFFERENT nodes (SDXL had them overlap); this exercises `inject_loras`'s separate-node path that Cycle 5 wrote but Cycle 6's tests didn't exercise.
- `app/registry/workflows.py` (extend) — dual-source LoRA injection correctness test for FLUX-style graphs.
- `config/models.yaml` (update) — add `chroma-hd-q8` entry with `clip_l`, `t5xxl`, `dual_clip_type: chroma`, `vram_estimate_gb: 9.0`, `prediction: eps` (FLUX isn't vpred).
- `app/backends/comfyui.py` (extend) — `unload_models()` calls `/free {unload_models: true, free_memory: true}`, probes `/system_stats` until VRAM drops below a threshold.
- `app/queue/worker.py` (extend) — track `last_model_name`; call `unload_models()` when next job's model differs. Mind the test fixture swap pattern (Cycle 3/4 tests swap `worker._adapter`).
- `app/validation.py` (extend) — VRAM guard: `validated.n * model_cfg.vram_estimate_gb + lora_overhead <= VRAM_BUDGET_GB`. Error: `error_code="vram_budget_exceeded"`.
- Tests: `test_vram_guard.py`, `test_dual_source_injection.py`, `tests/integration/test_model_swap.py`.

**Kickoff commands:**
```bash
cd d:/Works/source/local-image-generator-service
bash scripts/workflow-gate.sh reset
bash scripts/workflow-gate.sh size M 7 5 1     # M: 5-7 files, 5+ logic, side effects
bash scripts/workflow-gate.sh phase clarify
```

---

## Open items to resolve during Cycle 7 CLARIFY

- **GGUF node set pinned?** Cycle 2 pinned ComfyUI-GGUF to `6ea2651`. Chroma uses `UnetLoaderGGUF` + related custom nodes. Verify the pinned commit still exports the set we need; bump if Chroma1-HD demands newer node versions. If a bump is needed, patch `docker/comfyui/custom-nodes.txt` + `COMFYUI_GGUF_REF`.
- **VRAM overhead constant.** LoRAs add `~strength × 50-300 MB` to VRAM on FLUX models. Simplest heuristic: reserve `0.5 GB × len(loras)` per request. Confirm this is a reasonable default or if we need a more sophisticated model.
- **Is the VRAM guard per-request or per-model?** Per-request makes more sense (n=4 batch doubles VRAM); per-model is simpler. Lean: per-request with `n` factored in.
- **`unload_models` probe — how do we know when VRAM is actually freed?** ComfyUI's `/free` is advisory (memory reference #5 — reference_comfyui_quirks.md). Options: (a) sleep 5 s and trust; (b) probe `/system_stats` until `vram_free > threshold`; (c) don't probe, rely on ComfyUI's automatic eviction on next load. Lean: (b) with a 30 s timeout.
- **Free VRAM before Chroma loads.** Host currently shows `17.2 / 24 GB` used before any model load (flagged in memory `project_context`). Chroma Q8 needs ~9 GB on top of NoobAI's ~7 GB = 16 GB. 17 GB ambient + 16 GB GPU jobs = 33 GB > 24 GB physical. User needs to stop whatever's holding that 17 GB before running the integration test.
- **Dual-source injection.** Cycle 5's `inject_loras` finds MODEL_SOURCE + CLIP_SOURCE anchors. SDXL has both on node 1 (`CheckpointLoaderSimple`). Chroma has them on separate nodes (UnetLoaderGGUF + DualCLIPLoader). Cycle 5 anticipated this case but has no test. Cycle 7 needs to either extend the injector OR add a test proving the separate-node path works.

---

## Environment facts (persistent across sessions)

- **Host:** Windows 11, Docker Desktop, NVIDIA Container Toolkit working.
- **GPU:** RTX 4090 visible in containers, CUDA 13.0, driver 581.80.
- **VRAM:** ~22 GB free cold; ~15 GB after NoobAI loaded. **Host is currently holding ~17 GB before any model load — will block Chroma Q8 until freed.**
- **ComfyUI sidecar:** `image-gen-comfyui:0.9.2` pinned (ComfyUI v0.9.2 + GGUF 6ea2651).
- **Port conflict:** `free-context-hub-minio-1` on 9000/9001; our dev Compose uses 127.0.0.1:9100/9101.
- **Model files:** `./models/checkpoints/NoobAI-XL-v1.1.safetensors` (6.6 GB) + `./models/vae/sdxl_vae.safetensors` (319 MB). Cycle 7 adds Chroma + t5xxl + clip_l + ae.
- **LoRA library:** `./loras/` — 280 `.safetensors` across ahegao/group_sex/hanfu/mics. 235 addressable, 45 with spaces/parens. Zero sidecars in the user-drop tree; fetched Cycle 6 LoRAs will land under `./loras/civitai/<slug>_<vid>.safetensors` with full sidecars.
- **MinIO bucket:** `image-gen` auto-ensured at boot. Objects at `generations/YYYY/MM/DD/<job_id>/<index>.png`.
- **Gateway URL format:** `http://127.0.0.1:8700/v1/images/<job_id>/<index>.png` (Bearer-auth'd).
- **Civitai fetcher:** `POST /v1/loras/fetch` admin-only. URLs from `civitai.com` + `civitai.red`; downloads go to `civitai.com` (shared backend). `CIVITAI_API_TOKEN` required for NSFW / gated content.
- **Queue:** asyncio.Queue(maxsize=20); SQLite count_active gate; worker + reaper + fetcher lifespan-managed tasks; hard-cancel shutdown (Cycle 10 adds graceful drain).
- **LoRA root source of truth:** `app.state.loras_root` resolved once at boot. Handler + worker re-validation both consume it.
- **Sidecar `last_used` touch:** debounced 5 min; wrapped in `asyncio.to_thread` off the hot path.
- **ComfyUI quirks** (see `memory/reference_comfyui_quirks.md`): ckpt_name no subdir prefix; GGUF folder hyphen; status_str discriminator; client_id per-adapter-instance; /free is advisory (needs probe loop for Cycle 7).
- **Runtime deps trap** (see `memory/feedback_runtime_correctness.md`): every runtime `import x` in `app/` must live in `[project.dependencies]`, not dev. Docker `--no-dev` catches it.
- **Middleware trap** (see `memory/feedback_middleware.md`): no `BaseHTTPMiddleware`; pure ASGI only; test transport uses `raise_app_exceptions=False`.

---

## Verify current state before starting next session

```bash
cd d:/Works/source/local-image-generator-service
git status                                        # clean on main
bash scripts/workflow-gate.sh status              # empty
# First-boot prereq on a fresh clone: both dirs are gitignored so each dev
# must create the stubs before compose will start.
mkdir -p models/loras data/comfyui-user
docker compose up -d --build image-gen-service    # rebuild for Cycle 6 writable ./loras mount + new env
until docker compose ps --format json comfyui | grep -q '"Health":"healthy"'; do sleep 5; done
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/health | jq .
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/v1/loras | jq '.data | length'
uv run pytest --ignore=tests/integration -q      # 281 passed / 2 skipped
uv run ruff check .                               # All checks passed

# Cycle 6 Civitai fetch live smoke (requires CIVITAI_API_TOKEN in service env):
REQ_ID=$(curl -s -X POST -H "Authorization: Bearer $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://civitai.com/models/<id>?modelVersionId=<vid>"}' \
  http://127.0.0.1:8700/v1/loras/fetch | jq -r .request_id)
watch -n2 "curl -s -H \"Authorization: Bearer $ADMIN_KEY\" http://127.0.0.1:8700/v1/loras/fetch/$REQ_ID | jq ."
```

---

## External dependencies

- **Chroma1-HD model files (Cycle 7):** user needs to download `chroma1-hd-q8.gguf` (~9 GB), `t5xxl_fp8_e4m3fn.safetensors`, `ae.safetensors`, `clip_l.safetensors` and place them under `./models/` before Cycle 7 integration.
- **Free VRAM (Cycle 7 blocker):** ~17 GB currently held by some host process before any model load. Must be freed before Chroma integration can run.
- **LoreWeave integration-guide PR (Cycle 11):** user-owned, parallel. Soft-blocks Cycle 10 prod acceptance.

---

## What NOT to do next session

- Do not weaken the downloadUrl host allowlist added in Cycle 6 `/review-impl` (MED-1). Real Civitai downloads go to `*.civitai.com` CDN hosts; anything outside the allowlist is an SSRF risk.
- Do not remove the sidecar `last_used` debounce. Without it, 20 loras × 10 req/s = 200 writes/s — measured disk amplification. See memory `feedback_log_levels.md` pattern.
- Do not flip `LORA_MAX_CONCURRENT_FETCHES` above 1 without reviewing the eviction TOCTOU recheck — currently serialized under the semaphore, so recheck is sound.
- Do not trust Civitai's `sizeKB` as authoritative. The mid-stream disk recheck (MED-4 fix) is the guard; keep it.
- Do not reuse the `jobs` table for new async workflows. Cycle 6 chose a separate `lora_fetches` table deliberately; mixing domains muddies recovery/TTL logic.
- Do not bypass the registry vpred guard (Cycle 5). A full `inject_vpred` implementation lands when a vpred model is actually needed, not before.
- Do not use `BaseHTTPMiddleware` for any new middleware. See `memory/feedback_middleware.md`.
- Do not forget: new runtime `import x` in `app/` → update `[project.dependencies]`, rebuild Docker image (`memory/feedback_runtime_correctness.md`).
