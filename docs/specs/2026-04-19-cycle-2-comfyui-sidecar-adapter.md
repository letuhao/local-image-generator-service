# Spec — Cycle 2: ComfyUI sidecar + BackendAdapter + NoobAI workflow + anchor resolver

> **Cycle:** 2 of 11 · **Size:** XL (10 files, 6 logic changes, 1 side effect)
> **Parent plan:** [docs/plans/2026-04-18-image-gen-service-build.md §Cycle 2](../plans/2026-04-18-image-gen-service-build.md)
> **Arch refs:** §4.3 (adapter), §4.4 (registry), §4.7 (sidecar Dockerfile), §5 (compose), §8 (model roster), §9 (anchors), §13 (error codes)
> **Author:** agent (letuhao1994 approved 2026-04-19)

---

## 1. Goal (verbatim from plan, amended for v1.1)

> Calling our adapter's `generate()` with a hardcoded prompt produces a PNG from real ComfyUI running in a sibling container, using an anchor-tagged workflow template. No HTTP endpoint yet — adapter is directly testable.

Done means:
- `docker compose up comfyui` builds a pinned, reproducible sidecar image — real ComfyUI + pinned `city96/ComfyUI-GGUF` custom nodes, GPU-reserved.
- `ComfyUIAdapter().generate(job, model_cfg)` returns `GenerationResult` with PNG bytes for a hardcoded test workflow against NoobAI v1.1.
- Anchor-tagged workflow template loads via `app/registry/workflows.py`, validates required anchors, and find-by-anchor resolves correct node ids.
- Adapter handles WS canonical completion, WS disconnect → one reconnect attempt → polling fallback, timeout enforcement, `/interrupt` + `/free` on cancellation.
- Unit tests for anchor resolver green; integration test (marked `@pytest.mark.integration`, GPU required) produces a valid PNG.

## 2. Arch amendments locked in by user decisions (v0.5)

Two decisions in CLARIFY change the arch spec. Lands as v0.5 during this cycle, alongside the code:

### 2.1 Model roster: v1.1 eps replaces Vpred-1.0

- Day-1 SDXL model is **`NoobAI-XL-v1.1`** (epsilon prediction), not the Vpred-1.0 variant arch v0.4 §8 called for.
- **Rationale:** v1.1 is NoobAI's current stable — the team reset to eps after vpred training proved unstable. eps works with standard SDXL tooling and default sampler/scheduler settings, which matters for a service LoreWeave calls without tuning. Vpred's benefits (dynamic range at high CFG with rescale) are subjective and require per-request sampler tuning we can't rely on from callers.
- **Downstream effect:** no `ModelSamplingDiscrete` node is needed. Arch §9's "vpred injection" algorithm is deferred indefinitely — it'll be re-introduced in Cycle 5 alongside LoRA graph injection **only if** a future model brings back vpred.

### 2.2 Models directory layout: full ComfyUI tree under `./models/`

- v0.4 §5 mounted `./models:/workspace/ComfyUI/models/checkpoints:ro` (checkpoints-only).
- v0.5 amends to `./models:/workspace/ComfyUI/models:ro` — the whole ComfyUI models tree under one host directory.
- **Rationale:** ComfyUI expects standard subdirs (`checkpoints/`, `vae/`, `loras/`, etc.) under `models/`. External VAE (`sdxl_vae.safetensors` from stabilityai) lives in `vae/`, checkpoints in `checkpoints/`. `./loras/` keeps its separate mount per v0.4 §5.

Resulting layout (already on disk):
```
models/
├── checkpoints/
│   └── NoobAI-XL-v1.1.safetensors          7.1 GB
└── vae/
    └── sdxl_vae.safetensors                335 MB
```

Arch §4.4 `config/models.yaml` example updates: `checkpoint: checkpoints/NoobAI-XL-v1.1.safetensors`, `vae: vae/sdxl_vae.safetensors`, `prediction` field removed (or set to `eps`).

## 3. Decisions locked in CLARIFY

| Q | Decision |
|---|---|
| Q1 ComfyUI tag | Pin to **latest stable release tag** at BUILD time (via `git ls-remote --tags` + manual pick of newest `v*` semver non-pre-release). Exact tag captured in `docker/comfyui/custom-nodes.txt` + Dockerfile `ARG COMFYUI_REF=...`. Rebuilds pin to that ARG. |
| Q2 ComfyUI-GGUF commit | Pin to **latest commit on main** of `city96/ComfyUI-GGUF` at BUILD time. Same mechanism: captured in `custom-nodes.txt` + `ARG COMFYUI_GGUF_REF=...`. Chroma isn't in Cycle 2 but we pin now to avoid a rebuild in Cycle 7. |
| Q3 Model files | **Placed.** `models/checkpoints/NoobAI-XL-v1.1.safetensors` and `models/vae/sdxl_vae.safetensors` verified on host. HF download extras in `models/Laxhar-noobai-XL-1.1/` and `models/stabilityai-sdxl-vae/` still present — cleanup deferred pending user OK. |
| Q4 WS reconnect policy | One reconnect attempt on WS disconnect with 1s backoff. Second disconnect (or reconnect failure) → fall back to polling `/history/{prompt_id}` every `COMFY_POLL_INTERVAL_MS` until terminal state or `JOB_TIMEOUT_S` elapsed. Reconnect does not reset the `JOB_TIMEOUT_S` budget. |
| Q5 `client_id` scope | One uuid4 per `ComfyUIAdapter` instance, set at `__init__`. Reused across all `submit()` calls. One long-lived WS connection per adapter instance, managed lazily (connect on first `wait_for_completion`, reconnect on disconnect per Q4). WS events filtered by `prompt_id` to route to the correct pending future. |
| Q6 vpred bake vs runtime inject | Moot after v1.1 swap — no vpred in this cycle. Arch §9 vpred injection algorithm remains documented for future models; implementation deferred to Cycle 5 or later when actually needed. |

## 4. In scope (this cycle only)

- `docker/comfyui/Dockerfile` — `nvidia/cuda:12.4.1-runtime-ubuntu22.04`, Python 3.11 via `deadsnakes` PPA, `uv` for pip installs, ComfyUI cloned + checked out at pinned ref, `ComfyUI-GGUF` cloned + checked out at pinned commit into `custom_nodes/`, non-root user, `HEALTHCHECK` using `/system_stats`.
- `docker/comfyui/custom-nodes.txt` — textual pin list: `comfyui:<tag>` + `comfyui-gguf:<sha>`. Committed to repo for reproducibility.
- `docker/comfyui/entrypoint.sh` — launches `python main.py --listen 0.0.0.0 --port 8188 --preview-method none`. Propagates `COMFY_EXTRA_ARGS` env for debug.
- `docker-compose.yml` — replace the `image: nginx:alpine` placeholder comfyui with `build: ./docker/comfyui`; add `deploy.resources.reservations.devices` for nvidia GPU (count 1, capabilities: [gpu]); expand volumes to cover full `models/` tree + `./workflows:/workspace/ComfyUI/user/default/workflows:ro`; remove the nginx placeholder config file.
- `docker/comfyui-placeholder/` — **delete** this directory (no longer used).
- `workflows/sdxl_eps.json` — anchor-tagged SDXL workflow for NoobAI v1.1. Nodes: `CheckpointLoaderSimple` (`%MODEL_SOURCE%` + `%CLIP_SOURCE%` + `%LORA_INSERT%`), `VAELoader` (sdxl_vae), `CLIPTextEncode` x2 (`%POSITIVE_PROMPT%`, `%NEGATIVE_PROMPT%`), `EmptyLatentImage`, `KSampler` (`%KSAMPLER%`), `VAEDecode`, `SaveImage` (`%OUTPUT%`).
- `app/registry/__init__.py`, `app/registry/workflows.py` — `load_workflow(path) -> dict`, `validate_anchors(graph, required_anchors) -> None`, `find_anchor(graph, name) -> str` (returns node id). Raises `WorkflowValidationError` on missing/duplicate anchors.
- `app/backends/__init__.py`, `app/backends/base.py` — `BackendAdapter` Protocol (submit/wait/fetch_outputs/cancel/free/health), `GenerationResult` dataclass (list[bytes] + metadata), `BackendError` exception hierarchy (`ComfyUnreachableError`, `ComfyNodeError`, `ComfyTimeoutError` — all mapping to arch §13 error codes).
- `app/backends/comfyui.py` — `ComfyUIAdapter` class with all methods. Uses `httpx.AsyncClient` (shared, lifetime = adapter) for HTTP + `websockets` for WS.
- Deps: `httpx>=0.27,<0.28` (already present), `websockets>=13,<14` (new).
- `tests/test_anchor_resolver.py` — 5 tests: load valid workflow, missing anchor raises, duplicate anchor raises, find-by-anchor returns correct id, anchor in `_meta.title` recognized (not just `title`).
- `tests/integration/__init__.py`, `tests/integration/test_comfyui_adapter.py` — marked `@pytest.mark.integration`. Starts comfyui via docker-compose (outside pytest — assumes `docker compose up -d comfyui` was run), instantiates `ComfyUIAdapter`, submits a 1-step 256×256 NoobAI prompt, asserts PNG magic bytes in result.
- `.env.example` — add `COMFY_POLL_INTERVAL_MS=1000`, `JOB_TIMEOUT_S=300`, `COMFYUI_WS_URL=ws://comfyui:8188/ws`.
- `docs/architecture/image-gen-service.md` — amendment v0.5 §20 change log entry covering §2.1 + §2.2 above.

## 5. Out of scope (explicit descope)

- **No HTTP endpoint.** `POST /v1/images/generations` is Cycle 3. Adapter is directly testable via pytest — that's the goal.
- **No LoRA injection.** `inject_loras(graph, loras)` is Cycle 5. Workflow loads with LoRA chain empty.
- **No S3 upload.** `app/storage/s3.py` is Cycle 3. Adapter returns raw PNG bytes; no upload call.
- **No queue.** Adapter is called directly from tests, not from a worker. Cycle 4.
- **No model registry loading.** `config/models.yaml` + `app/registry/models.py` is Cycle 3. For Cycle 2 the test passes a hardcoded `ModelConfig` dataclass literal.
- **No Chroma.** Cycle 7. We DO pin ComfyUI-GGUF in Cycle 2's Dockerfile so Cycle 7 doesn't rebuild, but we don't use it.
- **No vpred.** See §2.1. Arch §9 runtime injection deferred.
- **No retry on adapter HTTP calls.** Single attempt per call this cycle; retry wrapping added in Cycle 4 alongside the queue worker.
- **No graceful shutdown of WS.** Adapter tears down WS on `close()`; no crash-safe persistence of in-flight prompts until Cycle 4's restart recovery.
- **No `/v1/models` endpoint.** Cycle 3.
- **No anchor `%VAE%`.** External VAE file is hardcoded in the workflow; anchor not needed until we have multiple models with different VAE nodes (Cycle 7).

## 6. File plan (final list)

| # | Path | Kind | Notes |
|---|---|---|---|
| 1 | `docker/comfyui/Dockerfile` | new | CUDA 12.4 runtime, Python 3.11, pinned ComfyUI + GGUF nodes, non-root `HEALTHCHECK` |
| 2 | `docker/comfyui/custom-nodes.txt` | new | pin list committed to repo |
| 3 | `docker/comfyui/entrypoint.sh` | new | `python main.py --listen 0.0.0.0 --port 8188 ...` |
| 4 | `workflows/sdxl_eps.json` | new | anchor-tagged SDXL workflow for NoobAI v1.1 |
| 5 | `app/registry/__init__.py` | new | package marker |
| 6 | `app/registry/workflows.py` | new | `load_workflow`, `validate_anchors`, `find_anchor` |
| 7 | `app/backends/__init__.py` | new | package marker |
| 8 | `app/backends/base.py` | new | `BackendAdapter` Protocol + `GenerationResult` + exception hierarchy |
| 9 | `app/backends/comfyui.py` | new | `ComfyUIAdapter` class |
| 10 | `tests/test_anchor_resolver.py` | new | 5 unit tests |
| 11 | `tests/integration/__init__.py` | new | package marker |
| 12 | `tests/integration/test_comfyui_adapter.py` | new | real-GPU integration test |
| 13 | `docker-compose.yml` | modify | swap placeholder → real comfyui build, GPU reservation, volumes |
| 14 | `pyproject.toml` | modify | add `websockets>=13,<14` |
| 15 | `.env.example` | modify | `COMFY_POLL_INTERVAL_MS`, `JOB_TIMEOUT_S`, `COMFYUI_WS_URL` |
| 16 | `docs/architecture/image-gen-service.md` | modify | v0.5 change log + §8 model-roster + §4.4 example + §5 volume update |
| 17 | `docker/comfyui-placeholder/` | delete | no longer used (was nginx placeholder) |

> 13–17 are outside the XL count of 10; they're configuration/doc/side-effect changes. XL classification driven by the 10 code/workflow files in the core column.

## 7. Test matrix (acceptance, not implementation detail)

### tests/test_anchor_resolver.py (unit, fast)
- `load_workflow(path)` returns a dict equal to the JSON file's parsed contents.
- `load_workflow` raises `WorkflowValidationError` on invalid JSON.
- `validate_anchors(graph, required=[...])` passes with a graph containing all required anchors.
- `validate_anchors` raises `WorkflowValidationError` listing missing anchors when any required anchor is absent.
- `validate_anchors` raises `WorkflowValidationError` when the same anchor name appears on two nodes (duplicate).
- `find_anchor(graph, "%KSAMPLER%")` returns the node id of the tagged node.
- `find_anchor(graph, "%MISSING%")` raises `KeyError` (no anchor → explicit error, not silent None).
- Anchor match is by `_meta.title == "<anchor>"` exactly — tests cover both presence and exact match (no substring match; `%MODEL_SOURCE_ALT%` does not satisfy `%MODEL_SOURCE%`).

### tests/integration/test_comfyui_adapter.py (integration, GPU)
- Marked `@pytest.mark.integration`; skipped in CI without GPU.
- **Prereq check:** fixture asserts `docker compose ps comfyui` shows "healthy" before running; if not, skip with a clear message.
- Instantiate `ComfyUIAdapter(http_url=..., ws_url=..., http_timeout_s=30, poll_interval_ms=1000)`.
- Build a minimal hardcoded NoobAI v1.1 workflow graph (copy of `workflows/sdxl_eps.json` loaded via `load_workflow` + fields populated).
- Call `await adapter.submit(graph)` → returns `prompt_id` (string).
- Call `await adapter.wait_for_completion(prompt_id, timeout_s=120)` → returns without raising within 120 s.
- Call `await adapter.fetch_outputs(prompt_id)` → returns at least one entry with `bytes` that begin with PNG magic `\x89PNG\r\n\x1a\n`.
- Call `await adapter.health()` → returns `{"status": "ok", "vram_free_gb": <float>}`.
- Call `await adapter.free()` → no exception raised.
- Post-test: verify `/system_stats` returned from ComfyUI shows VRAM was freed.

### Unit tests on adapter (without GPU)
- `submit` serializes `{"prompt": graph, "client_id": <uuid4>}` correctly (mock httpx).
- `submit` raises `ComfyNodeError` on `node_errors` in response body.
- `submit` raises `ComfyUnreachableError` on connection refused.
- `wait_for_completion` detects canonical `{"type":"executing","data":{"node":null,"prompt_id":...}}` on WS and returns.
- `wait_for_completion` filters WS events — messages with a different `prompt_id` are ignored (simulate: send unrelated WS event, assert function still waiting).
- `wait_for_completion` reconnects WS once on disconnect, resumes waiting.
- `wait_for_completion` falls back to polling after reconnect fails; terminates on polled `history[prompt_id].status.completed == true`.
- `wait_for_completion` raises `ComfyTimeoutError` when total elapsed > `JOB_TIMEOUT_S`.
- `cancel(prompt_id)` calls `POST /interrupt` for a running prompt and `DELETE /queue` for a queued prompt.
- `free()` calls `POST /free` with `{"unload_models": true, "free_memory": true}`.

These are added to `tests/test_comfyui_adapter.py` (new file, NOT the integration one).

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| ComfyUI tag moves, build breaks on rebuild | Tag SHA captured in `custom-nodes.txt` + Dockerfile ARG; rebuild always pins to the exact commit |
| `city96/ComfyUI-GGUF` custom node incompatible with ComfyUI tag | BUILD-time smoke test in Dockerfile (`python -c "from custom_nodes.ComfyUI_GGUF import *"`) fails the build fast |
| NoobAI v1.1 workflow needs specific SDXL refiner workflow | Plan confirms single-stage SDXL is sufficient (no refiner); refiner workflow deferred to a future cycle if quality is insufficient |
| WS `clientId` filter semantics wrong — we receive events for other jobs | Single-worker architecture in Cycle 2 means at most one prompt in flight — filter-by-prompt_id test still covers the logic, but real collision can't happen until Cycle 4 |
| Model volume mount read-only breaks ComfyUI's need to write metadata | ComfyUI reads checkpoints read-only; writes go to its own `output/` and `temp/` (separate, writable). Mount is safe. |
| VRAM spike during first `submit` exceeds 12 GB budget | Workflow is hardcoded to `EmptyLatentImage` 256×256 for the integration test — ~2 GB peak. Production safeguard is Cycle 7's VRAM guard. |
| Windows bind-mount permissions on ComfyUI container | Docker Desktop on NTFS handles this transparently for read-only mounts; confirmed already working for the nginx placeholder mount. Run as non-root user inside container. |
| `websockets` library version drift across Python minor versions | Pinned `websockets>=13,<14` matches arch §14 |
| First build downloads ~2 GB of torch + CUDA wheels | Use `uv` in the Dockerfile for faster installs; cache pip deps in a build stage. Initial build ≈ 5-10 min, rebuilds ≈ 30 s if only app code changed. |

## 9. Open items — none blocking BUILD

All CLARIFY items resolved. Tag/commit pins are resolved "at BUILD time via `git ls-remote`" and captured in `custom-nodes.txt`. Model files in place. Directory layout decided. vpred moot.

Two small items deferred to during BUILD (not blocking):
- Exact `uv` version for the Dockerfile (match host's 0.9.11).
- Whether to use `apt-get install python3.11` or download from deadsnakes PPA — stylistic choice; pick at BUILD A1 based on image size.

## 10. Self-review checklist

- [x] No placeholders, no TBDs
- [x] Every file in §6 has a stated purpose + test coverage in §7
- [x] Arch amendments (§2.1, §2.2) are explicitly identified and will land as v0.5 in the same cycle
- [x] Descope scans for Cycle 3+ leakage: no LoRA injection, no S3, no queue, no HTTP endpoint, no registry loading
- [x] Contradictions vs arch: none after the v0.5 amendment
- [x] Risks are specific (no generic "things might break")
- [x] Plan's Cycle 2 verification command still executes unchanged (integration test against real comfyui + PNG magic bytes check)

---

## 11. Design — concrete API contracts

### 11.1 `app/registry/workflows.py`

```python
class WorkflowValidationError(Exception):
    """Workflow JSON failed anchor validation or was not parseable."""

REQUIRED_ANCHORS_SDXL: tuple[str, ...] = (
    "%MODEL_SOURCE%", "%CLIP_SOURCE%", "%LORA_INSERT%",
    "%POSITIVE_PROMPT%", "%NEGATIVE_PROMPT%", "%KSAMPLER%", "%OUTPUT%",
)

def load_workflow(path: str | Path) -> dict[str, dict]:
    """Parse the JSON workflow file. Returns the ComfyUI prompt-API graph dict."""

def validate_anchors(graph: dict[str, dict], required: Sequence[str]) -> None:
    """Confirm each required anchor appears exactly once in any node's _meta.title.
    Raises WorkflowValidationError listing missing and duplicate anchors.
    """

def find_anchor(graph: dict[str, dict], anchor: str) -> str:
    """Return the node id whose _meta.title == anchor. Raises KeyError if absent."""
```

### 11.2 `app/backends/base.py`

```python
class BackendError(Exception):
    """Base for all backend-adapter errors. Subclasses map to arch §13 codes."""
    error_code: str = "internal"

class ComfyUnreachableError(BackendError):
    error_code = "comfy_unreachable"

class ComfyNodeError(BackendError):
    error_code = "comfy_error"

class ComfyTimeoutError(BackendError):
    error_code = "comfy_timeout"

@dataclass(frozen=True, slots=True)
class ModelConfig:
    name: str
    backend: Literal["comfyui"]
    workflow_path: str
    checkpoint: str          # relative to models/
    vae: str | None          # relative to models/; None means use checkpoint's baked-in VAE
    vram_estimate_gb: float
    defaults: dict           # sampler/scheduler/cfg/etc. — used in Cycle 3+
    limits: dict             # hard caps — used in Cycle 3+

@dataclass(frozen=True, slots=True)
class GenerationResult:
    images: list[bytes]      # PNG bytes per output
    prompt_id: str
    duration_ms: float

class BackendAdapter(Protocol):
    async def submit(self, graph: dict) -> str: ...                 # → prompt_id
    async def wait_for_completion(self, prompt_id: str, timeout_s: float) -> None: ...
    async def fetch_outputs(self, prompt_id: str) -> list[bytes]: ...
    async def cancel(self, prompt_id: str) -> None: ...
    async def free(self) -> None: ...
    async def health(self) -> dict: ...
    async def close(self) -> None: ...                              # tears down WS + HTTP client
```

### 11.3 `app/backends/comfyui.py`

```python
class ComfyUIAdapter:
    def __init__(
        self,
        *,
        http_url: str,                      # e.g. "http://comfyui:8188"
        ws_url: str,                        # e.g. "ws://comfyui:8188/ws"
        http_timeout_s: float = 30.0,
        poll_interval_ms: int = 1000,
    ) -> None:
        self._client_id = uuid4().hex
        self._http = httpx.AsyncClient(base_url=http_url, timeout=http_timeout_s)
        self._ws_url = ws_url
        self._ws: WebSocketClientProtocol | None = None
        self._ws_lock = asyncio.Lock()
        self._poll_interval_s = poll_interval_ms / 1000
        # Futures keyed by prompt_id; resolved when canonical completion received.
        self._pending: dict[str, asyncio.Future[None]] = {}

    async def submit(self, graph: dict) -> str: ...
    async def wait_for_completion(self, prompt_id: str, timeout_s: float) -> None: ...
    async def fetch_outputs(self, prompt_id: str) -> list[bytes]: ...
    async def cancel(self, prompt_id: str) -> None: ...
    async def free(self) -> None: ...
    async def health(self) -> dict: ...
    async def close(self) -> None: ...

    # Internals:
    async def _ensure_ws(self) -> None: ...     # lazy connect + one reconnect attempt
    async def _ws_reader(self) -> None: ...     # background task; filters by prompt_id
    async def _poll_fallback(self, prompt_id: str, deadline: float) -> None: ...
```

Key behaviors (tested per §7):

- `_ws_reader` runs as a background `asyncio.Task` spawned on first `_ensure_ws()`. Reads WS messages in a loop, looks for `{"type":"executing","data":{"node":null,"prompt_id": <pid>}}`, resolves `self._pending[pid]`.
- On `ConnectionClosed`, `_ensure_ws()` retries once with 1s backoff. Second failure → `wait_for_completion` switches to `_poll_fallback`.
- `_poll_fallback` hits `GET /history/{prompt_id}` every `_poll_interval_s` until `history[prompt_id]` exists with status != running. Respects the caller's `timeout_s` via `asyncio.timeout`.
- `fetch_outputs` reads `history[prompt_id].outputs`, iterates nodes with `_meta.title == "%OUTPUT%"`, streams PNG bytes for each `images[]` entry via `GET /view?filename=...&subfolder=...&type=output`.
- `cancel` reads current queue state via `GET /queue`; if prompt_id is in the queue (not yet running), `DELETE /queue {"delete":[prompt_id]}`; if running, `POST /interrupt`.
- `free` posts `{"unload_models": true, "free_memory": true}` to `/free`, then polls `/system_stats` up to 10s until VRAM drops.
- `close` cancels the `_ws_reader` task, closes WS, closes HTTP client.

### 11.4 Workflow file format — `workflows/sdxl_eps.json`

ComfyUI prompt-API format. Node ids are strings. Required anchors tagged via `_meta.title`:

```json
{
  "1": {
    "class_type": "CheckpointLoaderSimple",
    "inputs": {"ckpt_name": "checkpoints/NoobAI-XL-v1.1.safetensors"},
    "_meta": {"title": "%MODEL_SOURCE%"}
  },
  "2": {
    "class_type": "VAELoader",
    "inputs": {"vae_name": "vae/sdxl_vae.safetensors"}
  },
  "3": {
    "class_type": "CLIPTextEncode",
    "inputs": {"text": "", "clip": ["1", 1]},
    "_meta": {"title": "%POSITIVE_PROMPT%"}
  },
  "4": {
    "class_type": "CLIPTextEncode",
    "inputs": {"text": "worst quality, low quality", "clip": ["1", 1]},
    "_meta": {"title": "%NEGATIVE_PROMPT%"}
  },
  "5": {
    "class_type": "EmptyLatentImage",
    "inputs": {"width": 1024, "height": 1024, "batch_size": 1}
  },
  "6": {
    "class_type": "KSampler",
    "inputs": {
      "seed": 0, "steps": 28, "cfg": 5.0,
      "sampler_name": "euler_ancestral", "scheduler": "karras",
      "denoise": 1.0,
      "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0]
    },
    "_meta": {"title": "%KSAMPLER%"}
  },
  "7": {
    "class_type": "VAEDecode",
    "inputs": {"samples": ["6", 0], "vae": ["2", 0]}
  },
  "8": {
    "class_type": "SaveImage",
    "inputs": {"filename_prefix": "image-gen", "images": ["7", 0]},
    "_meta": {"title": "%OUTPUT%"}
  }
}
```

Note:
- `%MODEL_SOURCE%`, `%CLIP_SOURCE%`, `%LORA_INSERT%` all point at node `"1"` (`CheckpointLoaderSimple`) — SDXL has model at slot 0 and CLIP at slot 1 of the same loader. Plan's `find_anchor("%CLIP_SOURCE%")` returns `"1"`, and the LoRA injection algorithm (Cycle 5) uses slot 1 for CLIP from this specific loader class.
- For Cycle 2, `%CLIP_SOURCE%` and `%LORA_INSERT%` anchors are validated but unused. They must be present for Cycle 5's injection code to work unchanged.
- **Multi-anchor same-node convention:** `_meta.title` is a single string, not an array. To tag one node with multiple anchors, set `_meta.title` to a comma-separated string `"%MODEL_SOURCE%,%CLIP_SOURCE%,%LORA_INSERT%"` and have `find_anchor` split on commas when matching. Tested in §7.

### 11.5 Dockerfile shape — `docker/comfyui/Dockerfile`

```dockerfile
ARG COMFYUI_REF=<tag-pinned-at-build-A1>
ARG COMFYUI_GGUF_REF=<sha-pinned-at-build-A1>

FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS base

# Python 3.11 + system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.11 python3.11-venv python3-pip git curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# uv for faster installs
COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /uv

# Non-root user
RUN useradd --uid 1000 --create-home --shell /bin/bash comfy
WORKDIR /workspace
RUN chown comfy:comfy /workspace
USER comfy

# Clone pinned ComfyUI
RUN git clone --depth 1 --branch ${COMFYUI_REF} https://github.com/comfyanonymous/ComfyUI.git

# Clone pinned GGUF custom nodes
WORKDIR /workspace/ComfyUI/custom_nodes
RUN git clone https://github.com/city96/ComfyUI-GGUF.git && \
    cd ComfyUI-GGUF && git checkout ${COMFYUI_GGUF_REF}

# Install ComfyUI deps + GGUF deps into a shared venv via uv
WORKDIR /workspace/ComfyUI
RUN /uv venv /workspace/.venv --python 3.11 && \
    /uv pip install --python /workspace/.venv/bin/python -r requirements.txt && \
    /uv pip install --python /workspace/.venv/bin/python -r custom_nodes/ComfyUI-GGUF/requirements.txt

# Build-time smoke test: custom nodes import cleanly
RUN /workspace/.venv/bin/python -c "import sys; sys.path.insert(0, '/workspace/ComfyUI'); \
    from custom_nodes.ComfyUI_GGUF.nodes import NODE_CLASS_MAPPINGS; \
    assert 'UnetLoaderGGUF' in NODE_CLASS_MAPPINGS"

COPY --chown=comfy:comfy entrypoint.sh /workspace/entrypoint.sh
RUN chmod +x /workspace/entrypoint.sh

EXPOSE 8188
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8188/system_stats || exit 1

ENTRYPOINT ["/workspace/entrypoint.sh"]
```

### 11.6 Volume mounts on comfyui service

New `docker-compose.yml` comfyui block (base, no ports — dev override opens 8188):

```yaml
comfyui:
  build:
    context: ./docker/comfyui
    args:
      COMFYUI_REF: ${COMFYUI_REF:-v0.3.49}
      COMFYUI_GGUF_REF: ${COMFYUI_GGUF_REF:-main}
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  volumes:
    - ./models:/workspace/ComfyUI/models:ro
    - ./loras:/workspace/ComfyUI/models/loras:ro
    - ./workflows:/workspace/ComfyUI/user/default/workflows:ro
    # ComfyUI's output/ is container-local; uploader in Cycle 3 streams from /view.
  networks: [internal]
  healthcheck:
    test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8188/system_stats || exit 1"]
    interval: 10s
    timeout: 3s
    retries: 3
    start_period: 60s
  restart: unless-stopped
```

Note: `./loras` bind mount is added **now** even though LoRA injection is Cycle 5, because the compose file shouldn't be rewritten again then. The loras dir is created empty in Cycle 5; in Cycle 2, the mount source (`./loras`) must exist as a directory on the host or Docker Compose errors on `up`. Will `mkdir -p loras` as part of A1.

### 11.7 Error mapping (arch §13)

Adapter exceptions map to arch §13 error codes via the `error_code` class attribute:

| Exception | error_code |
|---|---|
| `ComfyUnreachableError` | `comfy_unreachable` |
| `ComfyNodeError` | `comfy_error` |
| `ComfyTimeoutError` | `comfy_timeout` |
| Uncaught `Exception` from adapter | `internal` (mapped by caller's handler, Cycle 3's API) |

### 11.8 Request lifecycle (Cycle 2 scope)

```
test / Cycle-3 handler
    │
    ▼
adapter.submit(graph)
    │   POST /prompt  {prompt: graph, client_id: <uuid4>}
    │   → returns prompt_id
    ▼
adapter.wait_for_completion(prompt_id, timeout_s)
    │   _ensure_ws() — connects to ws://.../ws?clientId=<uuid4>
    │   _ws_reader — background task loops, filters by prompt_id,
    │                resolves self._pending[prompt_id].set_result(None)
    │   on disconnect: one retry @ 1s → _poll_fallback(/history/{prompt_id})
    │   returns when future resolves, raises ComfyTimeoutError on elapsed > timeout_s
    ▼
adapter.fetch_outputs(prompt_id)
    │   GET /history/{prompt_id}
    │   for each node with _meta.title contains "%OUTPUT%":
    │     for each images[] entry:
    │       GET /view?filename=<fn>&subfolder=<sf>&type=<output>
    │       accumulate bytes
    │   returns list[bytes]
    ▼
caller (test or future API handler)
```

---

*End of spec.*
