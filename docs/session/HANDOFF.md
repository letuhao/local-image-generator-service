# Handoff — next session start here

> This file is **overwritten** every session close. It reflects **current state**, not history.
> History lives in [SESSION.md](SESSION.md). Architecture lives in [docs/architecture/image-gen-service.md](../architecture/image-gen-service.md). Build plan lives in [docs/plans/2026-04-18-image-gen-service-build.md](../plans/2026-04-18-image-gen-service-build.md).

**Last updated:** 2026-04-30 — Session closed after Sprint 14 / Cycle 11 docs deliverables.

---

## Where we are

- **Branch:** `main`. After commit: `git log origin/main..HEAD` for drift vs remote.
- **Plan progress:** **11 / 11** cycles complete (repo-side scope).

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
[x] 9  Webhook dispatcher
[x] 10 Startup validation + smoke test
[x] 11 LoreWeave integration-guide PR helper docs (this repo)
```

- **Workflow state:** run `bash scripts/workflow-gate.sh status` after pulling; reset if starting a fresh cycle.
- **Test suite:** baseline unit suite still healthy; Cycle 10 targeted verify:
  `uv run pytest -q tests/test_startup_checks.py tests/test_model_registry.py tests/integration/test_smoke_boot.py` → **22 passed / 1 skipped** (`RUN_SMOKE_BOOT_TEST=true` to enable compose integration).
- **Arch version:** v0.6 (unchanged).

---

## Next action

**Goal:** open/update the external LoreWeave repo PR using the prepared helper docs:

- `docs/integration/lore-weave-receiver-reference.md`
- `docs/integration/lore-weave-pr-ready-blocks.md`

**Kickoff:**

```bash
cd d:/Works/source/local-image-generator-service
bash scripts/workflow-gate.sh reset
bash scripts/workflow-gate.sh size XS 1 1 0
bash scripts/workflow-gate.sh phase clarify
```

**Cycle 11 completion notes (this repo):**

- Added receiver verification reference with Go snippet and explicit signature contract.
- Added PR-ready markdown blocks for LoreWeave external integration guide.
- Both docs align with architecture webhook contract and at-least-once dedupe requirements.

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

- **LoreWeave integration-guide PR (external repo):** user-owned; this repo now has ready-to-paste content.

---

## What NOT to do next session

- Do not weaken Cycle 6 Civitai `downloadUrl` host allowlist (SSRF).
- Do not remove LoRA `last_used` debounce.
- Do not bypass registry `vpred` boot guard without implementing `inject_vpred`.
- Do not use `BaseHTTPMiddleware`.
- Runtime imports in `app/` must stay in `[project.dependencies]` for Docker `--no-dev`.
