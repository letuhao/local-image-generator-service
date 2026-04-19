# Spec — Cycle 3: S3 uploader + model registry + sync POST /v1/images/generations + image fetch gateway

> **Cycle:** 3 of 11 · **Size:** L (7 core files, 5 logic areas, 1 side effect)
> **Parent plan:** [docs/plans/2026-04-18-image-gen-service-build.md §Cycle 3](../plans/2026-04-18-image-gen-service-build.md)
> **Arch refs:** §4.4 (registry), §4.6 (uploader), §6.0 (validation), §6.1 (sync endpoint), §6.4 (/v1/models), §11 (security), §13 (error codes)
> **Author:** agent (letuhao1994 approved 2026-04-19)

---

## 1. Goal

Per plan:
> A real HTTP request to `POST /v1/images/generations` with model=noobai returns a JSON body containing a URL. The generated PNG is fetchable at that URL for ≥ 1 h. LoreWeave could consume this right now.

Done means:
- `POST /v1/images/generations` with a valid body + Bearer key returns 200 with `{created, data: [{url}]}` plus `X-Job-Id` header.
- The returned URL points at **our service** (not MinIO directly) — `GET /v1/images/{job_id}/{index}.png` streams the PNG back.
- `GET /v1/models` returns the registry in OpenAI-compatible shape.
- All arch §6.0 bounds enforced via Pydantic; violations → 400 with `error_code=validation_error`.
- Startup validation: `config/models.yaml` loaded, files under `models/` exist, workflow anchors present, VRAM ≤ budget — service refuses to boot otherwise.

## 2. Arch amendments locked in by user decisions (v0.6)

One Q4 decision changes the contract. Lands as v0.6 during this cycle, alongside the code.

### 2.1 Backend gateway replaces presigned URLs

v0.4/v0.5 §4.6 specified **two boto3 clients** (internal for upload, public for presign) returning S3-presigned URLs directly to the caller.

v0.6 replaces this with a **backend gateway**:
- **One** boto3 client pointed at `S3_INTERNAL_ENDPOINT` only.
- `S3_PUBLIC_ENDPOINT` is removed from the active config surface (retained in `.env.example` as deprecated; Cycle 10 removes it entirely after verifying no downstream still reads it).
- `PRESIGN_TTL_S` is removed from the active config surface (same treatment).
- Response `data[].url` points at **our service** — format `{IMAGE_GEN_PUBLIC_BASE_URL}/v1/images/{job_id}/{index}.png`. In dev `IMAGE_GEN_PUBLIC_BASE_URL=http://127.0.0.1:8700`; in prod, the real ingress URL. The base URL is normalized at load time (trailing slashes stripped, scheme required) to prevent `//` in emitted URLs.
- A new endpoint `GET /v1/images/{job_id}/{index}.png` authenticates via `require_auth` (either scope), looks up the job in SQLite, validates index, streams the PNG bytes back from S3 with `Content-Type: image/png`.
- Auth posture: caller must present a valid Bearer key to fetch; no time-boxed URLs, no presign state. Same auth model as the POST endpoint.

**Rationale for the redirect:** gateway gives us unified auth (no separate presign credential surface), exact fetch observability (Cycle 4 orphan reaper sees every fetch directly, not via S3 bucket access logs), and simpler code (one client, one config path). Trade-off is bandwidth amplification through our uvicorn process — acceptable at the scale LoreWeave drives and revisitable in a later cycle if needed.

**What this does NOT change:**
- Arch §4.2 sync/async barrier, §6.2 async mode contract, §6.3 poll shape, §4.8 webhook dispatcher — all orthogonal.
- Orphan reaper semantics (Cycle 4): now keyed on "fetched via our GET endpoint" instead of "MinIO access log entry". Cleaner.
- Log redaction (§11): the "never log presigned URL" rule stays relevant because we may re-introduce signed URLs in a future cycle; for Cycle 3 we additionally never log S3 object keys outside `(bucket, key)` pairs.

### 2.2 Changes to specific arch sections

- §4.6 rewritten to describe gateway, retire presign.
- §5 topology: drop `S3_PUBLIC_ENDPOINT` from `image-gen-service` env; add `IMAGE_GEN_PUBLIC_BASE_URL`.
- §6.1 response URL example updated from `https://.../...png` to `https://<service>/v1/images/{id}/{index}.png`.
- §6.X new endpoint subsection: `GET /v1/images/{job_id}/{index}.png`.
- §11 security: gateway auth replaces presign-TTL rationale for image access.
- §13 error codes: unchanged (still `storage_error` on S3 failure, `not_found` on unknown job/index).

## 3. Decisions locked in CLARIFY

| Q | Decision |
|---|---|
| Q1 LoreWeave client timeout | **Not a Cycle-3 concern.** Server-side we set `JOB_TIMEOUT_S=300` (arch §12) and `size_max_pixels=1572864` (1024×1536) per arch §4.4. If LoreWeave's client times out before our generation, that's their configuration. Documented in arch §6.1. |
| Q2 MinIO bucket init | **Lifespan-managed.** `S3Storage.ensure_bucket()` called from `app.main` lifespan after `store.connect()`. Idempotent (`head_bucket` → create if 404). Boot fails fast if MinIO is unreachable. |
| Q3 Presign TTL | **Moot after §2.1** — no presign in Cycle 3. Removed from `.env.example`. |
| Q4 Public vs internal S3 | **Backend gateway** (see §2.1). One client, internal endpoint only. Response URL points at our service; `GET /v1/images/{job_id}/{index}.png` streams from S3. |
| Q5 Empty-output handling | **500 + `error_code=internal`.** Logged at ERROR. Response envelope `{"error":{"code":"internal","message":"ComfyUI returned zero outputs"}}`. |
| Q6 `response_format=b64_json` | **Ship both.** Always upload to S3 (orphan reaper depends on it). If caller requests `b64_json`, include base64 inline in `data[].b64_json`; still include `X-Job-Id` header; no `url` field in that case. |
| Q7 PNG magic pre-upload validation | **Enabled.** After `adapter.fetch_outputs()`, each byte block must start with `\x89PNG\r\n\x1a\n`. Mismatch → raise `ComfyNodeError("non-PNG bytes from ComfyUI")` → 500. |
| (clarifying) image-fetch auth scope | **Either scope** (`require_auth`). Generation or admin keys both allowed; no per-key job ownership tracking in Cycle 3. |

## 4. In scope (this cycle only)

- `app/storage/__init__.py`, `app/storage/s3.py` — `S3Storage` class (single boto3 client), `ensure_bucket()`, `upload_png(job_id, index, bytes) -> (bucket, key)`, `get_object(bucket, key) -> bytes`, `object_key_for(job_id, index) -> str`. Uploads wrapped in `tenacity.retry` per arch §4.6 (3 attempts, 500ms/1.5s/4.5s jittered exponential). `get_object` not retried (GET is idempotent from caller's POV anyway; a single failed read surfaces as 503 to the user).
- `app/registry/models.py` — `Registry` class, `load_registry(path)` factory, `get(name)` lookup, startup validation (checkpoint file exists, vae file exists, workflow file parses + passes `validate_anchors`, `vram_estimate_gb ≤ VRAM_BUDGET_GB`). On failure raises `RegistryValidationError` with a specific message; lifespan catches and logs `startup_failed{stage,reason}` before re-raising to exit non-zero.
- `config/models.yaml` — one entry for `noobai-xl-v1.1` matching arch v0.5 §4.4 (eps prediction, `checkpoints/NoobAI-XL-v1.1.safetensors`, `vae/sdxl_vae.safetensors`, `workflow: workflows/sdxl_eps.json`, defaults + limits).
- `app/api/images.py` — the endpoint file. Holds:
  - `POST /v1/images/generations` sync handler.
  - `GET /v1/images/{job_id}/{index}.png` gateway handler.
- `app/api/models.py` — `GET /v1/models` reading from the registry. (Package namespacing disambiguates from `app/registry/models.py`; imports stay clean via full paths.)
- `app/validation.py` — Pydantic `GenerateRequest` per arch §6.0 (prompt/negative_prompt/model/size/n/steps/cfg/seed/sampler/scheduler/response_format/mode). `mode=async` rejected with `error_code=async_not_enabled`. Webhook + LoRA fields NOT accepted (return `validation_error` if present — ruff's extra="forbid" on Pydantic). `model` validator looks the name up in the registry; `size` validator enforces `width*height ≤ model.limits.size_max_pixels`; `n`/`steps` validators use `model.limits`.
- `app/main.py` (modify) — lifespan extended: after `store.connect()`, load registry + instantiate S3Storage + `await s3.ensure_bucket()` + instantiate ComfyUIAdapter; wire all to `app.state`. Register `images_router` + `models_router`.
- `pyproject.toml` (modify) — add `boto3>=1.35,<2`, `tenacity>=9`, `PyYAML>=6.0.2`.
- `.env.example` (modify) — add `IMAGE_GEN_PUBLIC_BASE_URL=http://127.0.0.1:8700` (dev default); mark `S3_PUBLIC_ENDPOINT` and `PRESIGN_TTL_S` as deprecated (keep rows with comments, remove in Cycle 10).
- `docker-compose.yml` (modify) — pass `IMAGE_GEN_PUBLIC_BASE_URL` through to the service container from host env (`${IMAGE_GEN_PUBLIC_BASE_URL:-http://127.0.0.1:8700}`).
- `docs/architecture/image-gen-service.md` — v0.6 amendment §20 change log + §4.6 rewrite + §5 env update + §6.1 example + new §6.X fetch endpoint + §11 note.
- Tests:
  - `tests/test_validation.py` — every §6.0 bound enforced; webhook/lora/mode=async rejection.
  - `tests/test_model_registry.py` — YAML round-trip; missing checkpoint → startup error; workflow anchor missing → startup error; VRAM-over-budget → startup error.
  - `tests/test_s3_storage.py` — upload + get round-trip against mocked boto3 (via `moto`? or hand-roll?) — **use `moto`** for deterministic S3 behavior. Confirm retry on transient error. Confirm bucket ensure is idempotent.
  - `tests/test_sync_endpoint.py` — mocked adapter + mocked S3; happy path returns `{created, data}` with URL; validation error for oversized prompt; non-existent model → 400; b64_json returns inline base64; empty adapter output → 500 internal; wrong-auth-key → 401.
  - `tests/test_image_get.py` — fetch happy path; unknown job id → 404; index out of range → 404; unauth → 401.
  - `tests/integration/test_e2e_sync.py` — real ComfyUI + real MinIO. Asserts 200 + URL fetchable + content-type `image/png` + image size matches request.

## 5. Out of scope (explicit descope)

- **No queue, no disconnect handler, no restart recovery** — Cycle 4. Sync handler calls adapter directly, blocks entire request duration. Multiple concurrent requests serialize via ComfyUI's internal queue.
- **No LoRA support** — Cycle 5. Pydantic rejects `loras` field.
- **No Civitai fetch** — Cycle 6.
- **No Chroma / second model** — Cycle 7. Registry has one entry.
- **No async mode** — Cycle 8. Pydantic rejects `mode=async` with `async_not_enabled`.
- **No webhook** — Cycle 9. Pydantic rejects `webhook` field.
- **No model swap unload** — Cycle 7 (single model doesn't need it).
- **No per-request rate limiting** — deferred.
- **No retention-policy enforcement** (beyond orphan reaper which is Cycle 4).
- **No CDN / signed-URL fallback** — may revisit post-v1 if bandwidth becomes an issue.

## 6. File plan (final list)

| # | Path | Kind | Notes |
|---|---|---|---|
| 1 | `app/storage/__init__.py` | new | package marker |
| 2 | `app/storage/s3.py` | new | `S3Storage` class, tenacity-wrapped upload, bucket ensure, get |
| 3 | `app/registry/models.py` | new | `Registry` + `load_registry` + `ModelConfig` (dataclass already in app/backends/base.py; this module extends with YAML loader) |
| 4 | `config/models.yaml` | new | single noobai-xl-v1.1 entry |
| 5 | `app/validation.py` | new | Pydantic `GenerateRequest` per arch §6.0 |
| 6 | `app/api/images.py` | new | POST + GET image handlers |
| 7 | `app/api/models.py` | new | GET /v1/models |
| 8 | `app/main.py` | modify | lifespan: registry load + s3.ensure_bucket + adapter init; mount 2 routers |
| 9 | `pyproject.toml` | modify | +boto3 +tenacity +PyYAML |
| 10 | `.env.example` | modify | +IMAGE_GEN_PUBLIC_BASE_URL, deprecate S3_PUBLIC_ENDPOINT + PRESIGN_TTL_S |
| 11 | `docker-compose.yml` | modify | pass IMAGE_GEN_PUBLIC_BASE_URL env |
| 12 | `docs/architecture/image-gen-service.md` | modify | v0.6 change log + §4.6 rewrite + §5 + §6.1 + §6.X + §11 |
| 13 | `tests/test_validation.py` | new | ~18 tests covering each bound |
| 14 | `tests/test_model_registry.py` | new | ~8 tests covering YAML + validation |
| 15 | `tests/test_s3_storage.py` | new | ~6 tests covering upload/get/retry/ensure |
| 16 | `tests/test_sync_endpoint.py` | new | ~10 tests covering the POST surface with mocks |
| 17 | `tests/test_image_get.py` | new | ~6 tests covering the GET gateway |
| 18 | `tests/integration/test_e2e_sync.py` | new | real ComfyUI + MinIO |

> Plan-level count was 7. Actual: 7 core code + 1 config file + 3 config/doc modifications + 6 test files = 17. XL-adjacent but the XL threshold (per script) is 10 core files, not counting tests/config — we're at 7. L is correct.

## 7. Test matrix (acceptance, not implementation detail)

### tests/test_validation.py (~18 tests)
- `prompt` empty → validation_error; 8001 chars → validation_error; exactly 8000 chars → ok.
- `negative_prompt` 2001 chars → validation_error; 0 chars → ok.
- `model` not in registry → validation_error.
- `size` malformed (`"1024"`) → validation_error; over `size_max_pixels` → validation_error; `1024x1024` ok.
- `n` 0 → error; `n_max+1` → error; `n_max` ok.
- `steps` 0 → error; `steps_max+1` → error.
- `cfg` -1 → error; 31 → error; 0 ok; 30 ok.
- `seed` -2 → error; -1 ok; 2^53 ok; 2^53+1 → error.
- `sampler` not in allowed enum → error.
- `scheduler` not in allowed enum → error.
- `response_format` other than `url`/`b64_json` → error.
- `mode=async` when `ASYNC_MODE_ENABLED=false` → `async_not_enabled`.
- `mode=sync` (explicit) → ok.
- `webhook: {...}` present → `validation_error` (Cycle 9 will enable).
- `loras: [...]` present → `validation_error` (Cycle 5 will enable).
- Unknown fields → `validation_error` (Pydantic `extra="forbid"`).
- Minimum valid body (just prompt + model, defaults fill rest) → ok.

### tests/test_model_registry.py (~8 tests)
- `load_registry(valid_yaml)` returns Registry with one entry.
- Missing checkpoint file → `RegistryValidationError(stage="checkpoint_missing")`.
- Missing VAE file → `RegistryValidationError(stage="vae_missing")`.
- Workflow file absent → `RegistryValidationError(stage="workflow_missing")`.
- Workflow JSON missing required anchors → `RegistryValidationError(stage="anchors_missing")`.
- `vram_estimate_gb > VRAM_BUDGET_GB` → `RegistryValidationError(stage="vram_over_budget")`.
- YAML with zero entries → `RegistryValidationError(stage="empty_registry")`.
- `Registry.get("unknown")` → `KeyError`.

### tests/test_s3_storage.py (~6 tests, using moto)
- `ensure_bucket()` creates bucket if absent; second call is no-op.
- `upload_png(job_id="gen_abc", index=0, data=b"\x89PNG...")` writes to key matching `generations/YYYY/MM/DD/gen_abc/0.png`; returns `(bucket, key)`.
- Upload retries on `ClientError` from moto; terminal failure after 3 attempts → `StorageError`.
- `get_object(bucket, key)` returns bytes for an existing object.
- `get_object` for missing key → `StorageNotFoundError`.
- `ensure_bucket()` on an unreachable endpoint → raises (fail-fast at boot).

### tests/test_sync_endpoint.py (~10 tests)
- Missing Bearer → 401.
- Valid POST + mocked adapter returning one PNG + mocked S3 → 200 `{created, data: [{url}]}`; URL format `http://testserver/v1/images/gen_XXX/0.png`; `X-Job-Id` header present.
- `response_format=b64_json` → 200 `{data: [{b64_json}]}`; no `url` field; still `X-Job-Id` header.
- Adapter returns zero bytes → 500 with `error_code=internal`.
- Adapter returns non-PNG bytes → 500 with `error_code=internal` (from `_raise_if_not_png`).
- `adapter.submit` raises `ComfyNodeError` → 500 `comfy_error`.
- `adapter.wait_for_completion` raises `ComfyTimeoutError` → 500 `comfy_timeout`.
- S3 upload fails after retries → 500 `storage_error`.
- `n=2` → `data` has 2 entries.
- Validation error (prompt too long) → 400 `validation_error` + field pointer.

### tests/test_image_get.py (~6 tests)
- GET with valid Bearer + known job_id + valid index → 200 `Content-Type: image/png`, body == stored bytes.
- GET without Bearer → 401 (handler uses `require_auth`).
- GET for unknown job_id → 404 `not_found`.
- GET for index out of range → 404 `not_found`.
- GET for known job in `queued` status → 404 (outputs not yet written) with `not_found`.
- GET with admin key → 200 (either scope ok).

### tests/integration/test_e2e_sync.py (1-2 tests, real stack)
- POST with minimal prompt → 200 + URL.
- Follow the URL with `httpx.get(Authorization: Bearer)` → 200 + PNG bytes that start with magic + size ~matches request (1024×1024).

## 8. Data flow (sync request, Cycle 3)

```
client                                                        comfyui     minio
  │                                                             │           │
  │── POST /v1/images/generations  Bearer …  {model,prompt}────▶│           │
  │                                                             │           │
  │   app.api.images:                                            │           │
  │   1. validate (Pydantic) → GenerateRequest                   │           │
  │   2. registry.get(model) → ModelConfig                       │           │
  │   3. create_queued(store, model_name, input_json) → Job      │           │
  │   4. load_workflow + deepcopy                                │           │
  │   5. overwrite %POSITIVE_PROMPT%/%NEGATIVE_PROMPT%/%KSAMPLER%│           │
  │   6. adapter.submit(graph) ─────────────────────────────────▶│           │
  │   7. set_running(store, job_id, prompt_id=…, client_id=…)    │           │
  │   8. adapter.wait_for_completion(prompt_id, JOB_TIMEOUT_S)   │           │
  │                                              ◀── WS ──────── │           │
  │   9. adapter.fetch_outputs(prompt_id) ──────────────────────▶│           │
  │                                              ◀── PNG ─────── │           │
  │  10. _raise_if_not_png(bytes)                                │           │
  │  11. for each image: s3.upload_png(job_id, i, bytes)─────────────────────▶│
  │  12. set_completed(store, job_id, output_keys=[…], result_json=…)        │
  │  13. build URL: f"{PUBLIC_BASE_URL}/v1/images/{job_id}/{i}.png"          │
  │◀── 200 {created, data: [{url}]}  + X-Job-Id header ─┘                    │
  │                                                                           │
  │── GET /v1/images/gen_abc/0.png  Bearer …──▶ app.api.images:              │
  │                                                1. require_auth           │
  │                                                2. get_by_id(job_id)      │
  │                                                3. validate index         │
  │                                                4. s3.get_object ────────▶│
  │                                                   ◀── bytes ──────────── │
  │◀── 200 Content-Type image/png + body bytes ─────────────────┘            │
```

## 9. Design — concrete API contracts

### 9.1 `app/storage/s3.py`

```python
class StorageError(Exception):
    """Terminal failure to upload or read from S3. Maps to arch §13 storage_error."""

class StorageNotFoundError(StorageError):
    """Object does not exist in S3."""

@dataclass(frozen=True, slots=True)
class S3Config:
    internal_endpoint: str          # e.g. http://minio:9000
    bucket: str                     # e.g. image-gen
    access_key: str
    secret_key: str
    region: str = "us-east-1"       # MinIO default

class S3Storage:
    def __init__(self, cfg: S3Config) -> None: ...
    async def ensure_bucket(self) -> None:
        """Idempotent. head_bucket; if 404, create_bucket. Raises StorageError on transport."""
    async def upload_png(self, job_id: str, index: int, data: bytes) -> tuple[str, str]:
        """Return (bucket, key). Retries 3x with jitter on ClientError."""
    async def get_object(self, bucket: str, key: str) -> bytes:
        """Fetch bytes. StorageNotFoundError on 404. Not retried."""

def object_key_for(job_id: str, index: int, *, now: datetime | None = None) -> str:
    """Return generations/YYYY/MM/DD/<job_id>/<index>.png. now=None → datetime.now(UTC)."""
```

Notes:
- boto3 is sync. We wrap the upload/get calls in `asyncio.to_thread` so they don't block the event loop. (aioboto3 was another option but adds a dep + complexity.)
- Tenacity decorator: `@retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=0.5, max=5.0), retry=retry_if_exception_type(ClientError))`.
- Object key function is pure + stateless — separately testable without mocking.

### 9.2 `app/registry/models.py`

```python
class RegistryValidationError(Exception):
    """Startup validation failed. .stage identifies which check; .reason has detail."""
    def __init__(self, stage: str, reason: str) -> None: ...

class Registry:
    def __init__(self, models: dict[str, ModelConfig]) -> None: ...
    def get(self, name: str) -> ModelConfig:   # KeyError if missing
        ...
    def names(self) -> list[str]: ...
    def all(self) -> list[ModelConfig]: ...

def load_registry(
    yaml_path: str | Path,
    *,
    models_root: str | Path,
    workflows_root: str | Path,
    vram_budget_gb: float,
) -> Registry:
    """Parse YAML, validate each entry exists-on-disk + workflow anchors + VRAM.
    Raises RegistryValidationError on first failure (fail-fast at startup)."""
```

The Cycle 2 `ModelConfig` dataclass in `app/backends/base.py` is extended with:
- `workflow_path` (already present)
- `checkpoint` (already present)
- `vae` (already present)
- `defaults: dict` (already present)
- `limits: dict` (already present)
- `prediction: Literal["eps","vpred"] = "eps"` (new; informational only in Cycle 3)
- `capabilities: dict` (new; for §6.4)
- `backend: Literal["comfyui"] = "comfyui"` (already present)

### 9.3 `app/validation.py`

```python
class GenerateRequest(pydantic.BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    prompt: str = Field(min_length=1, max_length=8000)
    negative_prompt: str = Field(default="", max_length=2000)
    size: str = Field(pattern=r"^\d{3,4}x\d{3,4}$", default="1024x1024")
    n: int = Field(ge=1, default=1)
    steps: int | None = None           # validated against model.limits after model lookup
    cfg: float = Field(ge=0, le=30, default=5.0)
    seed: int = Field(ge=-1, le=(2**53), default=-1)
    sampler: str = Field(default="euler_ancestral")
    scheduler: str = Field(default="karras")
    response_format: Literal["url", "b64_json"] = "url"
    mode: Literal["sync", "async"] = "sync"

ALLOWED_SAMPLERS: frozenset[str] = frozenset({
    "euler", "euler_ancestral", "heun", "dpm_2", "dpm_2_ancestral",
    "lms", "dpmpp_2s_ancestral", "dpmpp_sde", "dpmpp_2m", "dpmpp_2m_sde",
    "dpmpp_3m_sde", "ddim", "uni_pc",
})
ALLOWED_SCHEDULERS: frozenset[str] = frozenset({
    "normal", "karras", "exponential", "sgm_uniform", "simple", "ddim_uniform",
})

def resolve_and_validate(
    req: GenerateRequest, *, registry: Registry, async_mode_enabled: bool
) -> ValidatedJob:
    """Post-Pydantic phase: look up model, merge defaults, enforce limits, enforce mode flag.
    Returns a ValidatedJob carrying resolved ModelConfig + final field values."""
```

### 9.4 `app/api/images.py` — sync handler signature

```python
@router.post("/v1/images/generations")
async def create_image(
    request: Request,
    body: GenerateRequest,
    kid: str = Depends(require_auth),
) -> JSONResponse:
    ...

@router.api_route("/v1/images/{job_id}/{index_name}", methods=["GET", "HEAD"])
async def get_image(
    job_id: str,
    index_name: str,              # e.g. "0.png"
    request: Request,
    kid: str = Depends(require_auth),
) -> Response:
    # index_name must match /^\d+\.png$/. Anything else → 404 `not_found` (same
    # shape as unknown route — avoids leaking that job_id exists but extension
    # was wrong).
    ...
```

- POST returns `JSONResponse(content={"created": <int>, "data": [...]})` + `X-Job-Id` response header.
- GET returns `Response(content=bytes, media_type="image/png")`.

### 9.5 `config/models.yaml`

```yaml
models:
  - name: noobai-xl-v1.1
    backend: comfyui
    workflow: workflows/sdxl_eps.json
    checkpoint: checkpoints/NoobAI-XL-v1.1.safetensors
    prediction: eps
    vae: vae/sdxl_vae.safetensors
    capabilities:
      image_gen: true
    defaults:
      size: "1024x1024"
      steps: 28
      cfg: 5.0
      sampler: euler_ancestral
      scheduler: karras
      negative_prompt: "worst quality, low quality"
    limits:
      steps_max: 60
      n_max: 4
      size_max_pixels: 1572864   # 1024×1536 ceiling
    vram_estimate_gb: 7
```

### 9.6 Lifespan order update in `app/main.py`

```
startup:
  1. configure_logging(level=LOG_LEVEL, log_prompts=LOG_PROMPTS)
  2. store = JobStore(DATABASE_PATH); await store.connect()
  3. registry = load_registry("config/models.yaml", …, vram_budget_gb=VRAM_BUDGET_GB)
  4. s3 = S3Storage(S3Config.from_env()); await s3.ensure_bucket()
  5. adapter = ComfyUIAdapter(http_url=COMFYUI_URL, ws_url=COMFYUI_WS_URL, …)
  6. app.state = {store, registry, s3, adapter, keyset, public_base_url}
  7. log event="service.started", …

shutdown (reverse):
  await adapter.close()
  await store.close()
  (s3 + registry have no async resources)
```

If any startup step fails (e.g. RegistryValidationError, S3 unreachable, ComfyUI unreachable), the lifespan raises → uvicorn logs the failure → process exits non-zero. Arch §16 "fail-fast startup" respected.

### 9.7 Error envelope on the sync endpoint

| Condition | Status | `error.code` |
|---|---|---|
| Missing / invalid Bearer | 401 | `auth_error` |
| Body fails Pydantic | 400 | `validation_error` |
| Model name not in registry | 400 | `validation_error` |
| `mode=async` when `ASYNC_MODE_ENABLED=false` | 400 | `async_not_enabled` |
| `webhook` or `loras` field present | 400 | `validation_error` |
| `n` × `vram_estimate_gb` > budget (future Cycle 7 check) | 400 | `vram_budget_exceeded` (not yet wired) |
| ComfyUI down | 503 | `comfy_unreachable` |
| ComfyUI validation error | 500 | `comfy_error` |
| ComfyUI timeout | 504 | `comfy_timeout` |
| Non-PNG bytes / zero outputs from ComfyUI | 500 | `internal` |
| S3 upload failure after retries | 502 | `storage_error` |
| Everything else | 500 | `internal` |

### 9.8 Error envelope on the GET gateway

| Condition | Status | `error.code` |
|---|---|---|
| Missing / invalid Bearer | 401 | `auth_error` |
| Unknown job_id | 404 | `not_found` |
| Job not yet completed (no `output_keys`) | 404 | `not_found` |
| Index out of range | 404 | `not_found` |
| S3 object missing | 404 | `not_found` |
| S3 transport failure | 502 | `storage_error` |
| Everything else | 500 | `internal` |

## 10. Risks + mitigations

| Risk | Mitigation |
|---|---|
| boto3 blocking calls freeze event loop | Every boto3 call wrapped in `asyncio.to_thread(...)` |
| MinIO first-boot bucket-create race (two workers) | Only one lifespan runs (single process); ensure_bucket is idempotent anyway |
| `IMAGE_GEN_PUBLIC_BASE_URL` misconfigured → broken response URLs | Lifespan log emits the value at INFO on startup; operators see it |
| Large PNGs (>50 MB after Cycle 7) buffered in memory on gateway | For Cycle 3 (NoobAI ~2 MB @ 1024²), buffered fine. Track RSS; if it grows, switch to streaming StreamingResponse from boto3 iter_content |
| Lifespan race: registry validates `workflow` file that Cycle 2 integration test deleted/moved | Registry loads from `workflows/sdxl_eps.json` which is committed; no test mutates it |
| Pydantic `extra="forbid"` blocks future extensions | When new fields land, update the model in the same PR; this is the right default for a public API |
| moto doesn't faithfully simulate MinIO quirks | Integration test against real MinIO catches anything moto missed |
| Response URL uses `http://` in dev but caller expects `https://` | `IMAGE_GEN_PUBLIC_BASE_URL` is operator-controlled; they set the correct scheme per env |
| GET endpoint becomes a bandwidth bottleneck | Document as known limitation in arch v0.6; reconsider if throughput becomes a problem (post-v1) |
| Caller using generation key can enumerate all job ids via sequential ksuid | ksuid is time-sortable but 128-bit — not easily enumerable. Still: Cycle 3 has no per-key ownership check; noted as a posture limitation in arch v0.6 §11 |

## 11. Self-review checklist

- [x] No placeholders, no TBDs
- [x] Every file in §6 has a stated purpose + test coverage in §7
- [x] Arch amendments (§2.1) are explicit and will land as v0.6 in the same cycle
- [x] Descope scan: no Cycle 4+ leakage (no queue, no disconnect, no loras, no webhook, no async)
- [x] Error envelope covers every 4xx/5xx code the endpoint can emit
- [x] Plan's Cycle 3 verification command still executes unchanged after the gateway change (the `curl -X POST` part still returns a URL that works — just a different host)
- [x] Q4 redirect fully reflected — no references to `_presign_client` or `S3_PUBLIC_ENDPOINT` in the active design surface

---

*End of spec.*
