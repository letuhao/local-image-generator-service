# Handoff — next session start here

> This file is **overwritten** every session close. It reflects **current state**, not history.
> History lives in [SESSION.md](SESSION.md). Architecture lives in [docs/architecture/image-gen-service.md](../architecture/image-gen-service.md). Build plan lives in [docs/plans/2026-04-18-image-gen-service-build.md](../plans/2026-04-18-image-gen-service-build.md).

**Last updated:** 2026-04-19 — Session closed after Sprint 8 / Cycle 5.

---

## Where we are

- **Branch:** `main`, **1 commit ahead** of `origin/main` (Cycle 5; earlier cycles pushed).
- **Latest commits:**
  - `af615a5` feat(cycle-5): LoRA scanner + graph injection + GET /v1/loras + path-traversal defense  ← unpushed
  - `b9fb515` docs: session close — rewrite HANDOFF.md for Cycle 5 pickup
  - `6f850c8` feat(cycle-4): queue worker + disconnect handler + orphan reaper + restart recovery
- **Plan progress:** 6 / 11 cycles complete.

```
[x] 0  Repo bootstrap
[x] 1  FastAPI + auth + SQLite + logging
[x] 2  ComfyUI sidecar + adapter + NoobAI workflow
[x] 3  MinIO gateway + model registry + first sync endpoint
[x] 4  Queue + disconnect + reaper + restart
[x] 5  LoRA local + injection
[ ] 6  Civitai fetcher hardened                          ← NEXT (L, 1-2 day budget)
[ ] 7  Chroma model #2
[ ] 8  Async + polling
[ ] 9  Webhook dispatcher
[ ] 10 Startup validation + smoke test
[ ] 11 LoreWeave integration-guide PR (parallel, user-owned)
```

- **Workflow state:** retro pending close (will close with the Cycle 5 commit).
- **Test suite:** `uv run pytest -q --ignore=tests/integration` → **213 passed, 2 skipped** (Windows symlink gates). Integration suite (`-m integration`) untouched — requires live compose stack.
- **Arch version:** v0.6 (unchanged). Cycle 5 needed no arch revision.

---

## Next action (Sprint 9 = Cycle 6)

**Goal per plan §Cycle 6:** `POST /v1/loras/fetch {civitai_model_id, version_id?}` downloads the `.safetensors` + writes a full sidecar JSON, with LRU eviction enforcing `LORA_DIR_MAX_SIZE_GB`. Scanner picks it up on the next `GET /v1/loras`.

Files to create / modify:

- `app/loras/fetcher.py` — `CivitaiFetcher` class. Single-flight per-version (dedup concurrent fetches by `civitai_version_id`). Streaming download to `<name>.safetensors.tmp` + atomic rename. Sidecar written with `{sha256, source: "civitai", civitai_model_id, civitai_version_id, base_model_hint, trigger_words, fetched_at}`. Verifies SHA-256 if Civitai provides it; log warn + keep if not.
- `app/loras/eviction.py` — LRU eviction. On write, if `du(loras_root) > LORA_DIR_MAX_SIZE_GB * 1e9`, evict by `atime` ascending, never touching files used in a job in the last 7 days (check `input_json LIKE '%{name}%'`). Arch §17.
- `app/api/loras_fetch.py` (or fold into `app/api/loras.py`) — `POST /v1/loras/fetch` (admin scope). Body: `{civitai_model_id, version_id?}`. 202 with status poll URL; or synchronous for small (<200 MB) files.
- `app/validation.py` — `CivitaiFetchRequest` Pydantic model.
- `docker-compose.yml` — flip `./loras` mount from `:ro` to writable for the service (keep `:ro` on comfyui).
- Tests: `test_lora_fetcher.py` (download happy path + sha mismatch + 403 NSFW + network error + retries), `test_lora_eviction.py` (LRU order + protect-recent-use + min_free behavior), `test_loras_fetch_endpoint.py` (auth + validation + 202 response shape), integration test gated on `CIVITAI_API_TOKEN`.
- Env: `CIVITAI_API_TOKEN`, `LORA_DIR_MAX_SIZE_GB`, `LORA_MAX_CONCURRENT_FETCHES=1`, `LORA_MAX_SIZE_BYTES=2147483648` (2 GiB).

**Kickoff commands:**
```bash
cd d:/Works/source/local-image-generator-service
bash scripts/workflow-gate.sh reset
# Plan says Cycle 6 is L (~8-10 files). Script likely agrees.
bash scripts/workflow-gate.sh size L 8 6 1
bash scripts/workflow-gate.sh phase clarify
```

---

## Open items to resolve during Cycle 6 CLARIFY

- **Civitai API surface + auth.** Token goes in `Authorization: Bearer <CIVITAI_API_TOKEN>` header (Civitai's documented shape). Verify current API version (`/api/v1/models/{id}` or `/api/v1/model-versions/{id}`). Decide: fetch by `model_id` only → pick `modelVersions[0]` (latest), or require explicit `version_id`? Recommendation: support both; default to latest-version when `version_id` omitted.
- **Sync vs async download.** A 2 GiB safetensors over 100 Mbps = ~3 min. Holding a sync request for 3 min won't fly. Recommendation: always 202 accepted, poll via `GET /v1/loras/fetch/{request_id}`. Small downloads (<200 MB) could sync, but the poll path simplifies client code.
- **LRU eviction policy.** Pure access-time eviction can delete a LoRA mid-generation. Job-in-flight protection: skip eviction on LoRAs whose `name` appears in `input_json` of any non-terminal job, OR any job in the last 7 days. Recommendation: conservative — protect 7-day window + active jobs.
- **SHA-256 verification.** Civitai returns `hashes: {SHA256, BLAKE3, ...}`. Our download verifies against `SHA256` if provided. If Civitai doesn't provide one → log warn + accept, record `sha256=null` in the sidecar. Recommendation: strict when present, permissive when absent.
- **Disk-space pre-check.** Before download, confirm `shutil.disk_usage(loras_root).free >= expected_size * 2` (double to leave headroom for verification temp copies). If not, refuse 507 Insufficient Storage per arch §13.
- **Retry strategy.** Civitai returns 403 for NSFW without token (behavior flipped once — see memory `civitai` notes). On 403: surface error with auth hint, don't retry. On 5xx: tenacity exponential, 3 attempts. On connection reset: retry via HTTP Range resume if partial download exists. Recommendation: start simple (tenacity 3x, no range resume) and defer resume to a later cycle.

---

## Environment facts (persistent across sessions)

- **Host:** Windows 11, Docker Desktop, NVIDIA Container Toolkit working.
- **GPU:** RTX 4090 visible in containers, CUDA 13.0, driver 581.80.
- **VRAM:** ~22 GB free cold; ~15 GB after NoobAI loaded.
- **ComfyUI sidecar:** `image-gen-comfyui:0.9.2` pinned (ComfyUI v0.9.2 + GGUF 6ea2651).
- **Port conflict:** `free-context-hub-minio-1` on 9000/9001; our dev Compose uses 127.0.0.1:9100/9101.
- **Model files:** `./models/checkpoints/NoobAI-XL-v1.1.safetensors` (6.6 GB) + `./models/vae/sdxl_vae.safetensors` (319 MB).
- **LoRA library:** `./loras/` — 280 `.safetensors` across ahegao/group_sex/hanfu/mics subdirs, 42 GB total. 235 addressable, 45 have spaces/parens (surface via `/v1/loras` with `addressable=false`). Zero sidecars so far — Cycle 6 fetcher will populate them.
- **MinIO bucket:** `image-gen` auto-ensured at boot. Objects at `generations/YYYY/MM/DD/<job_id>/<index>.png`.
- **Gateway URL format:** `http://127.0.0.1:8700/v1/images/<job_id>/<index>.png` (Bearer-auth'd).
- **Queue:** asyncio.Queue(maxsize=20); SQLite count_active gate in handler; worker + reaper lifespan-managed tasks; hard-cancel shutdown (Cycle 10 adds graceful drain).
- **LoRA root source of truth:** `app.state.loras_root` (resolved once at boot from `LORAS_ROOT` env). Handler + worker re-validation both consume it — no per-request env re-read.
- **ComfyUI quirks** (see `memory/reference_comfyui_quirks.md`): ckpt_name no subdir prefix; GGUF folder hyphen; status_str discriminator; client_id per-adapter-instance; /free is advisory. ComfyUI's `LoraLoader` accepts `subdir/name` forms as `lora_name` — verified in Cycle 5.
- **Runtime deps trap** (see `memory/feedback_runtime_correctness.md`): every runtime `import x` in `app/` must live in `[project.dependencies]`, not dev. Docker `--no-dev` catches it.
- **Middleware trap** (see `memory/feedback_middleware.md`): no `BaseHTTPMiddleware`; pure ASGI only; test transport uses `raise_app_exceptions=False`.

---

## Verify current state before starting next session

```bash
cd d:/Works/source/local-image-generator-service
git status                                        # clean on main
bash scripts/workflow-gate.sh status              # empty
docker compose up -d                              # all 3 services; rebuilds image-gen-service for LORAS_ROOT+loras mount
until docker compose ps --format json comfyui | grep -q '"Health":"healthy"'; do sleep 5; done
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/health | jq .
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/v1/loras | jq '.data | length'  # expect ~280
uv run pytest -q --ignore=tests/integration      # 213 passed / 2 skipped
uv run ruff check .                               # All checks passed
# Live smoke with a LoRA:
FIRST_LORA=$(curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/v1/loras | jq -r '.data[] | select(.addressable) | .name' | head -1)
curl -s -X POST -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d "{\"model\":\"noobai-xl-v1.1\",\"prompt\":\"lora test\",\"size\":\"512x512\",\"steps\":4,\"loras\":[{\"name\":\"$FIRST_LORA\",\"weight\":0.8}]}" \
  http://127.0.0.1:8700/v1/images/generations | jq .
```

Note: before that smoke works, **rebuild the service image** so the container picks up the Cycle 5 `LORAS_ROOT` env + `./loras` bind mount. `docker compose up -d --build image-gen-service`.

---

## External dependencies

- **Civitai API token for Cycle 6** — user needs an account + generated API token. Set `CIVITAI_API_TOKEN=<token>` in `.env`. Needed for NSFW + authenticated downloads.
- **LoreWeave integration-guide PR (Cycle 11)** — user-owned, parallel. Soft-blocks Cycle 10 prod acceptance.

---

## What NOT to do next session

- Do not flip the `./loras:/app/loras` mount to writable before Cycle 6 BUILD lands — Cycle 5 left it `:ro` deliberately.
- Do not add a second `LORAS_ROOT` lookup path. `app.state.loras_root` is the single source of truth as of Cycle 5 (post-review MED-2 fix).
- Do not bypass the registry vpred guard. It refuses `prediction="vpred"` at boot by design (arch v0.5 defer). A full `inject_vpred` implementation lands when a vpred model is actually needed.
- Do not weaken the `count_active` gate — it's the only thing preventing SQLite row flood under request spikes.
- Do not use `BaseHTTPMiddleware` for any new middleware (recurring warning). See `memory/feedback_middleware.md`.
- Do not forget: new runtime `import x` in `app/` → update `[project.dependencies]`, rebuild Docker image (see `memory/feedback_runtime_correctness.md`).
- Do not trust `Literal[...]` annotations for YAML-loaded fields — pair with an explicit `frozenset` membership check. Cycle 3 gotcha, still applies.
- Do not expand the scanner to follow directory symlinks that escape root — MED-4 in Cycle 5 closed this on purpose.
