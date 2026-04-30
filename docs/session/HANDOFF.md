# Handoff — next session start here

> This file is **overwritten** every session close. It reflects **current state**, not history.
> History lives in [SESSION.md](SESSION.md). Architecture lives in [docs/architecture/image-gen-service.md](../architecture/image-gen-service.md). Build plan lives in [docs/plans/2026-04-18-image-gen-service-build.md](../plans/2026-04-18-image-gen-service-build.md).

**Last updated:** 2026-04-30 — Session in progress on Sprint 12 / Cycle 9 webhook dispatcher.

---

## Where we are

- **Branch:** `main`. After commit: `git log origin/main..HEAD` for drift vs remote.
- **Plan progress:** **9 / 11** cycles complete.

```
[x] 0  Repo bootstrap
[x] 1  FastAPI + auth + SQLite + logging
[x] 2  ComfyUI sidecar + adapter + NoobAI workflow
[x] 3  MinIO gateway + model registry + first sync endpoint
[x] 4  Queue + disconnect + reaper + restart
[x] 5  LoRA local + injection
[x] 6  Civitai fetcher hardened
[x] 7  Chroma model #2 + VRAM guard + model unload on swap
[x] 8  Async + polling
[ ] 9  Webhook dispatcher  ← NEXT
[ ] 10 Startup validation + smoke test
[ ] 11 LoreWeave integration-guide PR (parallel, user-owned)
```

- **Workflow state:** run `bash scripts/workflow-gate.sh status` after pulling; reset if starting a fresh cycle.
- **Test suite:** `uv run pytest --ignore=tests/integration -q` → **290 passed / 2 skipped** (Windows symlink gates). Integration: `uv run pytest -m integration -q tests/integration/` per module gates (`CIVITAI_API_TOKEN`, etc.).
- **Arch version:** v0.6 (unchanged).

---

## Next action (Sprint 12 = Cycle 9)

**Goal per plan §Cycle 9:** webhook dispatcher with signing/retry hardening and terminal delivery semantics. See `docs/plans/2026-04-18-image-gen-service-build.md` §Cycle 9.

**Kickoff:**

```bash
cd d:/Works/source/local-image-generator-service
bash scripts/workflow-gate.sh reset
bash scripts/workflow-gate.sh size <S-or-M> <files> <logic> <side_effects>
bash scripts/workflow-gate.sh phase clarify
```

**Cycle 9 current notes (in progress):**

- Core dispatcher + retries + admin endpoint are implemented and passing tests.
- Remaining work should follow your gate criteria before marking Cycle 9 done.

---

## Environment facts (persistent)

- **Chroma on disk (Cycle 7 layout):** `models/unet/chroma1-hd-q8.gguf`, `models/text_encoders/{clip_l,t5xxl_fp8_e4m3fn}.safetensors`, `models/vae/ae.safetensors` (paths in `config/models.yaml` match this layout).
- **VRAM guard (implemented):** `model.vram_estimate_gb + 0.064 * len(loras) ≤ VRAM_BUDGET_GB` (default budget 12); error `vram_budget_exceeded`.
- **Model swap:** worker calls `unload_models` when `model` changes; strict verify requires `/system_stats` baseline + increase after `/free`; failure → job `vram_budget_exceeded`.
- **Host / GPU:** Windows 11, Docker + NVIDIA toolkit; RTX 4090. Free ambient VRAM remains a practical limit for Chroma + NoobAI back-to-back — see arch / Comfy quirks.

---

## Verify after pull

```bash
cd d:/Works/source/local-image-generator-service
uv run pytest --ignore=tests/integration -q   # 290 passed / 2 skipped
uv run ruff check . && uv run ruff format --check .
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/v1/models | jq '.data[].id'
# expect: noobai-xl-v1.1 and chroma-hd-q8 (registry + model files present)
```

---

## External dependencies

- **LoreWeave integration-guide PR (Cycle 11):** user-owned; soft-blocks Cycle 10 prod acceptance.

---

## What NOT to do next session

- Do not weaken Cycle 6 Civitai `downloadUrl` host allowlist (SSRF).
- Do not remove LoRA `last_used` debounce.
- Do not bypass registry `vpred` boot guard without implementing `inject_vpred`.
- Do not use `BaseHTTPMiddleware`.
- Runtime imports in `app/` must stay in `[project.dependencies]` for Docker `--no-dev`.
