# Handoff — next session start here

> This file is **overwritten** every session close. It reflects **current state**, not history.
> History lives in [SESSION.md](SESSION.md). Architecture lives in [docs/architecture/image-gen-service.md](../architecture/image-gen-service.md). Build plan lives in [docs/plans/2026-04-18-image-gen-service-build.md](../plans/2026-04-18-image-gen-service-build.md).

**Last updated:** 2026-04-19 — Session closed after Sprint 7 / Cycle 4.

---

## Where we are

- **Branch:** `main`, **7 commits ahead** of `origin/main` (not pushed).
- **Commits since origin:**
  - `9b13ea9` feat(cycle-1): FastAPI auth + SQLite job store + structured JSON logging
  - `08e67aa` docs: session close — rewrite HANDOFF.md for Cycle 2 pickup
  - `2ff43a7` feat(cycle-2): ComfyUI sidecar + BackendAdapter + anchor-tagged NoobAI workflow
  - `b053021` docs: session close — rewrite HANDOFF.md for Cycle 3 pickup
  - `f9713aa` feat(cycle-3): MinIO gateway + model registry + POST /v1/images/generations
  - `d3e3c54` docs: session close — rewrite HANDOFF.md for Cycle 4 pickup
  - `6f850c8` feat(cycle-4): queue worker + disconnect handler + orphan reaper + restart recovery
- **Plan progress:** 5 / 11 cycles complete.

```
[x] 0  Repo bootstrap
[x] 1  FastAPI + auth + SQLite + logging
[x] 2  ComfyUI sidecar + adapter + NoobAI workflow
[x] 3  MinIO gateway + model registry + first sync endpoint
[x] 4  Queue + disconnect + reaper + restart
[ ] 5  LoRA local + injection                            ← NEXT (L, 1-2 day budget)
[ ] 6  Civitai fetcher hardened
[ ] 7  Chroma model #2
[ ] 8  Async + polling
[ ] 9  Webhook dispatcher
[ ] 10 Startup validation + smoke test
[ ] 11 LoreWeave integration-guide PR (parallel, user-owned)
```

- **Workflow state:** retro pending close (will close with this commit).
- **Test suite:** `uv run pytest -q` → 180 passed (177 unit + 3 integration).
- **Arch version:** v0.6 (unchanged across Cycle 4; only §4.2 got a recovery-seed note).

---

## Next action (Sprint 8 = Cycle 5)

**Goal per plan §Cycle 5:** A request with `loras: [{name, weight}]` produces a visibly different image than the same request without. Path-traversal attempts return 400.

Files to create:

- `app/loras/__init__.py` + `app/loras/scanner.py` — walk `./loras/`, return `LoraMeta` list (`name`, `filename`, `sha256`, `source`, `civitai_model_id?`, `civitai_version_id?`, `base_model_hint`, `trigger_words`). Sidecar file lives at `<name>.json` alongside the `.safetensors`.
- `app/api/loras.py` — `GET /v1/loras` (any auth scope per arch §6.5). Lists sidecars + hashes.
- `app/registry/workflows.py` extensions:
  - `inject_loras(graph: dict, loras: list[LoraSpec]) -> None` — per arch §9 algorithm: find `%MODEL_SOURCE%` + `%CLIP_SOURCE%` anchors, chain `LoraLoader` nodes, rewrite downstream consumers.
  - `inject_vpred(graph)` — arch §9 vpred injection. Scaffolded but no-op unless model_cfg.prediction == "vpred"; v0.5 deferred so v1.1 users never trigger it.
- `app/validation.py` — stop rejecting `loras` field. Add §6.0 rules: name regex `^[A-Za-z0-9_][A-Za-z0-9_\-.]*$`, weight float -2..2, ≤ 20 entries. Path-realpath containment check (reject `../` traversal).
- `app/queue/worker.py` — pipeline calls `inject_loras(graph, validated.loras)` after anchor substitution, BEFORE `adapter.submit`.
- Tests:
  - `tests/test_lora_scanner.py` — scans a temp directory with 3 loras + 1 sidecar-less + 1 malformed → correct LoraMeta list.
  - `tests/test_graph_injection.py` — inject 0, 1, 3 loras. Assert new node ids chained, downstream consumers rewritten, original graph not mutated.
  - `tests/test_path_traversal.py` — `loras: [{"name": "../../etc/passwd"}]` → 400 `validation_error`.
  - `tests/integration/test_lora_effect.py` — integration: generate with vs without LoRA, assert image hashes differ. Gated on a fixture LoRA being present in `./loras/`.

**Kickoff commands:**
```bash
cd d:/Works/source/local-image-generator-service
bash scripts/workflow-gate.sh reset
# Plan says Cycle 5 is L (~8 files). Script likely agrees.
bash scripts/workflow-gate.sh size L 8 5 1
bash scripts/workflow-gate.sh phase clarify
```

---

## Open items to resolve during Cycle 5 CLARIFY

- **`./loras/` mount decision (deferred from Cycle 2).** See arch v0.5 §20 change log: the options are (A) move LoRAs under `./models/loras/` and update the image-gen-service writable mount to `/app/models/loras`; (B) keep `./loras/` top-level and add `./loras:/workspace/ComfyUI/models/loras:ro` back on the comfyui service. **Pick one.** Recommendation: B. Preserves v0.4 semantics (service owns writes, ComfyUI reads); smaller blast radius than reshuffling `./models/`; easier Cycle 6 (Civitai fetch writes to a separate top-level dir).
- **LoRA file format requirements.** ComfyUI's `LoraLoader` accepts `.safetensors` only. Sidecar is `<name>.json` alongside, containing `{name, filename, sha256, source, civitai_*?, base_model_hint, trigger_words}`. Decide: scanner rejects loras without sidecars (strict, fail-loudly), or scanner tolerates missing sidecars and synthesizes a minimal LoraMeta from filename? Recommendation: tolerate (so dev can drop a `.safetensors` and test immediately); warn at INFO; Cycle 6's Civitai fetch writes full sidecars.
- **Graph mutation safety.** Injection mutates the graph dict. Worker does `copy.deepcopy(graph_template)` BEFORE injection, so mutation is safe. Confirm: the injected nodes use string keys like `f"{max_id+1}"`, not reuse. ComfyUI accepts any string key.
- **Fixture LoRA choice for integration test.** User needs to drop a real SDXL-compatible LoRA in `./loras/` before running the integration. Options: (a) pick one from Civitai (e.g., Illustrious-XL or NoobAI-compatible style LoRA, smallish ~150 MB); (b) skip integration test if absent (module-skip like the ComfyUI integration). Recommendation: (b) with a clear skip message pointing to a suggested download.
- **`inject_vpred` scaffolding.** Arch §9 (v0.5 note) defers vpred. For Cycle 5, write the function signature + body that's a no-op when `prediction != "vpred"`, and raise `NotImplementedError` if prediction IS "vpred" (so a future model YAML bump fails fast, not silently). Confirm.

---

## Environment facts (persistent across sessions)

- **Host:** Windows 11, Docker Desktop, NVIDIA Container Toolkit working.
- **GPU:** RTX 4090 visible in containers, CUDA 13.0, driver 581.80.
- **VRAM:** ~22 GB free cold; ~15 GB after NoobAI loaded.
- **ComfyUI sidecar:** `image-gen-comfyui:0.9.2` pinned (ComfyUI v0.9.2 + GGUF 6ea2651).
- **Port conflict:** `free-context-hub-minio-1` on 9000/9001; our dev Compose uses 127.0.0.1:9100/9101.
- **Model files:** `./models/checkpoints/NoobAI-XL-v1.1.safetensors` (6.6 GB) + `./models/vae/sdxl_vae.safetensors` (319 MB).
- **MinIO bucket:** `image-gen` auto-ensured at boot. Objects at `generations/YYYY/MM/DD/<job_id>/<index>.png`.
- **Gateway URL format:** `http://127.0.0.1:8700/v1/images/<job_id>/<index>.png` (Bearer-auth'd).
- **Queue:** asyncio.Queue(maxsize=20); SQLite count_active gate in handler; worker + reaper lifespan-managed tasks; hard-cancel shutdown (Cycle 10 adds graceful drain).
- **ComfyUI quirks** (see `memory/reference_comfyui_quirks.md`): ckpt_name no subdir prefix; GGUF folder hyphen; status_str discriminator; client_id per-adapter-instance; /free is advisory.
- **Runtime deps trap** (see `memory/feedback_runtime_correctness.md`): every runtime `import x` in `app/` must live in `[project.dependencies]`, not dev. Docker `--no-dev` catches it.
- **Middleware trap** (see `memory/feedback_middleware.md`): no `BaseHTTPMiddleware`; pure ASGI only; test transport uses `raise_app_exceptions=False`.

---

## Verify current state before starting next session

```bash
cd d:/Works/source/local-image-generator-service
git status                                        # clean on main
bash scripts/workflow-gate.sh status              # empty
docker compose up -d                              # all 3 services
until docker compose ps --format json comfyui | grep -q '"Health":"healthy"'; do sleep 5; done
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/health | jq .
uv run pytest -q                                  # 180 passed
uv run ruff check .                               # All checks passed
# Live smoke through the queue:
curl -s -X POST -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"noobai-xl-v1.1","prompt":"queue smoke","size":"512x512","steps":1}' \
  http://127.0.0.1:8700/v1/images/generations | jq .
```

---

## External dependencies

- **LoRA file for Cycle 5 integration test** — user to drop at least one SDXL LoRA (`.safetensors` + optional sidecar) into `./loras/` before running the Cycle 5 integration. Can skip this pre-req if we gate the integration test on file presence.
- **LoreWeave integration-guide PR (Cycle 11)** — user-owned, parallel. Soft-blocks Cycle 10 prod acceptance.

---

## What NOT to do next session

- Do not start Cycle 6 (Civitai fetch) before Cycle 5 lands — Cycle 6's LRU eviction relies on the scanner walking the directory correctly.
- Do not build `inject_vpred` beyond a scaffolded no-op. Arch v0.5 deferred vpred; a full implementation lands when a model YAML flips `prediction: vpred` (not in the roadmap).
- Do not modify the graph template in place — `copy.deepcopy(graph_template)` happens at the top of `worker._run_pipeline` already; preserve that.
- Do not weaken the `count_active` gate — it's the only thing preventing SQLite row flood under request spikes.
- Do not use `BaseHTTPMiddleware` for any new middleware (recurring warning). See `memory/feedback_middleware.md`.
- Do not forget: new runtime `import x` in `app/` → update `[project.dependencies]`, rebuild Docker image (see `memory/feedback_runtime_correctness.md`).
- Do not trust `Literal[...]` annotations for YAML-loaded fields — pair with an explicit `frozenset` membership check. Cycle 3 gotcha, still applies.
