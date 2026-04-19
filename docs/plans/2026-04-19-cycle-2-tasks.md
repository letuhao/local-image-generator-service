# Cycle 2 — task plan

> **Spec:** [docs/specs/2026-04-19-cycle-2-comfyui-sidecar-adapter.md](../specs/2026-04-19-cycle-2-comfyui-sidecar-adapter.md)
> **Size:** XL · **Execution mode:** Inline, sequential.
> **Commit strategy:** Single end-of-cycle commit (matches Cycle 1 pattern — one coherent vertical slice).

---

## Chunk A — Pin selection

### A1. Pick ComfyUI + ComfyUI-GGUF pins
**Files:** `docker/comfyui/custom-nodes.txt` (new)
**Intent:**
- `git ls-remote --tags https://github.com/comfyanonymous/ComfyUI.git | tail -15` → pick the newest non-rc stable `v*` tag.
- `git ls-remote https://github.com/city96/ComfyUI-GGUF.git HEAD` → get the current main-branch SHA.
- Write `custom-nodes.txt` with exact pins. Committed to repo for reproducibility.
**Verify:** file contains two lines, format `name ref  # human-comment`. `git show --name-only HEAD` after commit shows the file.

---

## Chunk B — Sidecar image + Compose

### B1. Dockerfile
**Files:** `docker/comfyui/Dockerfile`, `docker/comfyui/entrypoint.sh`
**Intent:** Per spec §11.5. Base `nvidia/cuda:12.4.1-runtime-ubuntu22.04`, Python 3.11, uv 0.9.11 for pip installs, clone ComfyUI at pinned ref, clone GGUF at pinned SHA into `custom_nodes/`, non-root `comfy` user (uid 1000), build-time `python -c "from ComfyUI_GGUF.nodes import NODE_CLASS_MAPPINGS"` smoke test, `HEALTHCHECK` against `/system_stats`.
**Verify:** `docker compose build comfyui` returns 0. Expected first-build time 5-10 min (torch + CUDA wheels).

### B2. Delete placeholder + wire real comfyui + create host dirs
**Files:** remove `docker/comfyui-placeholder/default.conf`, `docker/comfyui-placeholder/` directory; modify `docker-compose.yml` (swap `image: nginx:alpine` for `build: ./docker/comfyui` + GPU reservation + volumes per spec §11.6); `mkdir -p loras workflows` on host.
**Verify:** `docker compose config` renders the merged YAML without errors. Confirm comfyui service shows `build:` not `image: nginx:alpine` and has the `deploy.resources` + volumes block.

**Chunk B verification:** `docker compose build comfyui && docker compose up -d comfyui && docker compose ps comfyui` shows "healthy" within 120 s.

---

## Chunk C — Workflow + anchor resolver (TDD)

### C1. Workflow JSON
**Files:** `workflows/sdxl_eps.json`
**Intent:** Per spec §11.4 — 8 nodes (CheckpointLoaderSimple, VAELoader, 2× CLIPTextEncode, EmptyLatentImage, KSampler, VAEDecode, SaveImage) with the 7 required anchors on `_meta.title`. Multi-anchor on node `"1"` uses comma-separated convention.
**Verify:** `python -c "import json; json.load(open('workflows/sdxl_eps.json'))"` exits 0.

### C2. Red: anchor-resolver tests
**Files:** `tests/test_anchor_resolver.py`
**Intent:** 8 tests per spec §7:
- `load_workflow` returns parsed dict
- invalid JSON → `WorkflowValidationError`
- `validate_anchors` passes on complete graph
- missing anchor → `WorkflowValidationError` listing missing ones
- duplicate anchor name → `WorkflowValidationError`
- `find_anchor` returns correct node id
- unknown anchor → `KeyError`
- comma-separated multi-anchor convention (`"%A%,%B%,%C%"` matches all three)
**Verify:** `uv run pytest tests/test_anchor_resolver.py` fails with ImportError on `app.registry.workflows`.

### C3. Green: registry package
**Files:** `app/registry/__init__.py` (empty), `app/registry/workflows.py`
**Intent:** Implement `load_workflow`, `validate_anchors`, `find_anchor`, `WorkflowValidationError`. Match spec §11.1 signatures.
**Verify:** `uv run pytest tests/test_anchor_resolver.py` all green.

**Chunk C verification:** 8 anchor-resolver tests green.

---

## Chunk D — Backend Protocol + errors

### D1. Protocol + data types
**Files:** `app/backends/__init__.py` (empty), `app/backends/base.py`
**Intent:** `BackendAdapter` Protocol, `GenerationResult`, `ModelConfig` dataclasses, `BackendError` hierarchy (`ComfyUnreachableError`, `ComfyNodeError`, `ComfyTimeoutError`) per spec §11.2.
**Verify:** no tests yet (Protocol just has to import); `uv run python -c "from app.backends.base import BackendAdapter, ComfyTimeoutError"` exits 0.

---

## Chunk E — ComfyUI adapter (TDD, mocked)

### E1. Red: adapter unit tests
**Files:** `tests/test_comfyui_adapter.py`
**Intent:** Per spec §7 "Unit tests on adapter" — 10 tests using `respx` (mocks httpx) + a fake `WebSocketClientProtocol`. Cover submit happy path + node_errors + connection refused; wait_for_completion canonical WS event + prompt_id filtering + reconnect + poll fallback + timeout; cancel queued vs running; free + system_stats polling.
**Verify:** fails with ImportError on `app.backends.comfyui`.

### E2. Green: ComfyUIAdapter
**Files:** `app/backends/comfyui.py`
**Intent:** Per spec §11.3. `ComfyUIAdapter` class, `_ensure_ws`, `_ws_reader` as a background `asyncio.Task`, `_poll_fallback`, `submit`, `wait_for_completion` with one WS reconnect + poll fallback, `fetch_outputs`, `cancel`, `free`, `health`, `close`.
**Verify:** `uv run pytest tests/test_comfyui_adapter.py` all green.

Potential snag: `respx` is listed in the arch §14 dev-dep list but pyproject.toml forward-comp comment has it queued for Cycle 6. Need to add `respx>=0.21,<0.22` to dev deps in this cycle's G1 (or add it here ad-hoc before E1).

**Chunk E verification:** adapter unit tests green (mocked).

---

## Chunk F — Integration test (real GPU)

### F1. Integration test
**Files:** `tests/integration/__init__.py` (empty), `tests/integration/test_comfyui_adapter.py`
**Intent:** Marked `@pytest.mark.integration`. Fixture checks `docker compose ps comfyui` shows "healthy" (skip otherwise). Instantiates `ComfyUIAdapter`, loads `workflows/sdxl_eps.json`, submits a 1-step 256×256 prompt (tiny for VRAM + time), waits up to 120 s, fetches outputs, asserts PNG magic bytes `\x89PNG\r\n\x1a\n`, calls `free()`, confirms VRAM drops.
**Verify:** `docker compose up -d comfyui && uv run pytest -m integration tests/integration/test_comfyui_adapter.py` green. Expected runtime: ~30-90 s depending on cold-load.

**Chunk F verification:** integration test green; PNG written to container output + fetched via `/view` + bytes start with PNG magic.

---

## Chunk G — Deps + env

### G1. Pyproject additions
**Files:** `pyproject.toml`
**Intent:** Add `websockets>=13,<14` to runtime deps (arch §14). Add `respx>=0.21,<0.22` to dev deps. Update forward-compat comment to remove both. Run `uv sync`.
**Verify:** `uv sync` returns 0; lock diff shows both added.

### G2. Env example
**Files:** `.env.example`
**Intent:** Add `COMFYUI_WS_URL=ws://comfyui:8188/ws` (if not already present), `COMFY_POLL_INTERVAL_MS=1000`, `JOB_TIMEOUT_S=300`.
**Verify:** `grep -E 'COMFYUI_WS_URL|COMFY_POLL_INTERVAL_MS|JOB_TIMEOUT_S' .env.example` shows all three.

---

## Chunk H — Arch amendment

### H1. v0.5 change log + §8 + §4.4 + §5 updates
**Files:** `docs/architecture/image-gen-service.md`
**Intent:**
- §20 change log: add a v0.5 entry dated 2026-04-19 covering the two amendments (v1.1 replaces Vpred-1.0, `./models` full-tree mount).
- §8 model roster: replace "NoobAI-XL Vpred-1.0" with "NoobAI-XL v1.1"; drop vpred-specific language.
- §4.4 `config/models.yaml` example: update `checkpoint:`, remove `prediction: vpred`, update `vae:` path.
- §5 topology: change the volumes block on `image-gen-service` for the `./models` mount.
- §9 vpred injection paragraph: retitle "deferred — re-introduce only if a future model needs v-prediction".
**Verify:** `grep -c '^### v0.5' docs/architecture/image-gen-service.md` returns 1. §8 no longer references Vpred. §20 change log has the new entry.

---

## Chunk I — Verify

### I1. Pyproject + pytest + ruff (offline, no GPU)
**Verify:** `uv sync && uv run pytest -q -m "not integration"` → green (Cycle 1 + Cycle 2 non-integration tests). `uv run ruff check .` → clean. `uv run ruff format --check .` → clean.

### I2. Sidecar build + healthy boot
**Verify:** `docker compose build comfyui` → returns 0. `docker compose up -d comfyui` → container reaches "healthy" within 120 s. `docker compose logs comfyui --tail 20` shows `Started server` + no import errors.

### I3. Real-GPU integration test
**Verify:** `docker compose up -d` (all services) → `uv run pytest -m integration tests/integration/test_comfyui_adapter.py -v` → green within 120 s. Asserts PNG magic bytes + VRAM drops after `free()`.

### I4. Plan-verification command (from main plan Cycle 2)
**Verify:**
```
docker compose build comfyui && \
docker compose up -d comfyui && \
uv run pytest -m integration -q tests/integration/test_comfyui_adapter.py
```

---

## Order of execution (strict)

```
A1                          # pin selection
 ↓
G1 (deps)                   # websockets + respx needed before test code compiles
 ↓
B1 → B2                     # Dockerfile + compose (can run in parallel with C/D)
 ↓
C1 → C2 → C3                # workflow + TDD anchor resolver
 ↓
D1                          # Protocol + errors
 ↓
E1 → E2                     # TDD adapter
 ↓
F1                          # integration test
 ↓
G2                          # env vars (can be earlier; put here because it's lightweight)
 ↓
H1                          # arch v0.5 amendment
 ↓
I1 → I2 → I3 → I4           # verify
```

## Commit checkpoints

Single commit at cycle end. Message template:

```
feat(cycle-2): ComfyUI sidecar + adapter + anchor-tagged NoobAI workflow

- docker/comfyui/: pinned ComfyUI + city96/ComfyUI-GGUF custom nodes,
  CUDA 12.4, Python 3.11, non-root, build-time GGUF import smoke test.
- workflows/sdxl_eps.json: anchor-tagged SDXL workflow for NoobAI v1.1.
- app/registry/workflows.py: load + validate_anchors + find_anchor with
  comma-separated multi-anchor convention.
- app/backends/base.py: BackendAdapter Protocol + GenerationResult +
  error hierarchy mapping to arch §13 codes.
- app/backends/comfyui.py: HTTP + WebSocket with one reconnect,
  polling fallback, timeout enforcement, /interrupt + /free on cancel.
- tests/test_anchor_resolver.py (8), tests/test_comfyui_adapter.py (10
  mocked), tests/integration/test_comfyui_adapter.py (real GPU).
- docker-compose.yml: swap nginx placeholder for real comfyui build
  with GPU reservation + full models tree mount.
- arch v0.5: v1.1 eps replaces Vpred-1.0 (§8), ./models full-tree
  mount (§5), vpred injection deferred (§9).
```

---

## Risks during BUILD

| Risk | Mitigation during build |
|---|---|
| ComfyUI `git clone --depth 1 --branch <tag>` fails if tag was moved/deleted | A1 captures exact refs in `custom-nodes.txt`; CI rebuild always uses those, not HEAD |
| Torch wheel pull bandwidth | Expect 2-3 GB first build; no-cache rebuilds reuse layers after A1 runs once |
| ComfyUI `main.py` entry changed across versions | If B1 build passes but runtime fails, the pinned tag is incompatible with our entrypoint args; roll back A1 to a prior tag |
| GGUF custom node requires ComfyUI commit newer than our tag | Build-time smoke test in Dockerfile catches this; roll A1 to a newer ComfyUI tag or older GGUF commit |
| Integration test hits VRAM budget (17.2 / 24 GB already used on host) | Test uses 256×256 @ 1 step (<2 GB peak); should fit even under pressure. If it doesn't, stop other GPU processes on host. |
| Windows bind-mount read-only fails for ComfyUI | Same topology as Cycle 0's nginx placeholder which worked; `:ro` on `./models` is tested |
| `respx` + async httpx version mismatch | Pin `respx>=0.21` which supports httpx 0.27+ |
| WS reconnect test flakiness (race between `_ws_reader` task and test assertion) | Use `asyncio.Event` in tests to deterministically trigger reconnect rather than timing-based |
| `/loras` bind mount fails because `./loras/` doesn't exist on host | B2 `mkdir -p loras workflows` before `docker compose up` |
| Arch doc update breaks internal links | H1 uses careful find+replace; verify `grep '^## ' docs/architecture/image-gen-service.md | wc -l` unchanged |

---

*End of task plan.*
