# Cycle 3 — task plan

> **Spec:** [docs/specs/2026-04-19-cycle-3-s3-registry-sync-endpoint.md](../specs/2026-04-19-cycle-3-s3-registry-sync-endpoint.md)
> **Size:** L · **Execution mode:** Inline, sequential.
> **Commit strategy:** Single end-of-cycle commit (matches Cycles 1-2).

---

## Chunk A — Deps + env + arch amendment

### A1. Pyproject additions
**Files:** `pyproject.toml`
**Intent:** Add `boto3>=1.35,<2`, `tenacity>=9`, `PyYAML>=6.0.2`. Dev: `moto[s3]>=5,<6`. Run `uv sync`.
**Verify:** `uv sync` returns 0; lock diff includes all four.

### A2. Env surface
**Files:** `.env.example`
**Intent:** Add `IMAGE_GEN_PUBLIC_BASE_URL=http://127.0.0.1:8700`. Mark `S3_PUBLIC_ENDPOINT` + `PRESIGN_TTL_S` as deprecated (keep rows with comments).
**Verify:** `grep IMAGE_GEN_PUBLIC_BASE_URL .env.example` shows the row.

### A3. Compose passthrough
**Files:** `docker-compose.yml`
**Intent:** Pass `IMAGE_GEN_PUBLIC_BASE_URL` from host env into the image-gen-service container. Default to `http://127.0.0.1:8700` if unset.
**Verify:** `docker compose config | grep IMAGE_GEN_PUBLIC_BASE_URL` shows the mapping.

### A4. Arch v0.6 amendment
**Files:** `docs/architecture/image-gen-service.md`
**Intent:** §20 change log entry (gateway replaces presign). §4.6 rewrite. §5 env update (drop S3_PUBLIC_ENDPOINT / PRESIGN_TTL_S; add IMAGE_GEN_PUBLIC_BASE_URL). §6.1 response URL example. §6.X new "GET /v1/images/{id}/{index}.png" subsection. §11 note on gateway auth.
**Verify:** `grep -c '^### v0.6' docs/architecture/image-gen-service.md` = 1. §4.6 no longer mentions "presign". §6.X new subsection exists.

---

## Chunk B — Storage module (TDD with moto)

### B1. Red: S3 storage tests
**Files:** `tests/test_s3_storage.py`
**Intent:** 6 tests per spec §7 using `@mock_s3` (moto) — ensure_bucket idempotent, upload_png writes to dated key path, retry on transient ClientError, get_object round-trip, 404 → StorageNotFoundError, unreachable endpoint fails fast.
**Verify:** fails with ImportError on `app.storage.s3`.

### B2. Green: S3Storage class
**Files:** `app/storage/__init__.py` (empty), `app/storage/s3.py`
**Intent:** Per spec §9.1. `S3Config` dataclass, `S3Storage` class, `StorageError` + `StorageNotFoundError`, `object_key_for` helper (pure, testable without moto). boto3 calls wrapped in `asyncio.to_thread`; `tenacity.retry` on upload.
**Verify:** `uv run pytest tests/test_s3_storage.py -q` green.

---

## Chunk C — Registry (TDD)

### C1. Red: registry tests
**Files:** `tests/test_model_registry.py`
**Intent:** 8 tests per spec §7 — YAML round-trip; each RegistryValidationError stage (checkpoint_missing, vae_missing, workflow_missing, anchors_missing, vram_over_budget, empty_registry); `Registry.get("unknown")` raises KeyError.
**Verify:** fails with ImportError on `app.registry.models`.

### C2. Green: Registry module
**Files:** `app/registry/models.py`
**Intent:** Per spec §9.2. `RegistryValidationError(stage, reason)`, `Registry` class (holds dict), `load_registry(yaml_path, ...)` factory. Extend `ModelConfig` in `app/backends/base.py` with `prediction` + `capabilities` fields (default safe values).
**Verify:** `uv run pytest tests/test_model_registry.py -q` green.

### C3. Green: models.yaml
**Files:** `config/models.yaml`
**Intent:** Single noobai-xl-v1.1 entry per spec §9.5.
**Verify:** `load_registry("config/models.yaml", …)` succeeds at manual import; integration covered by lifespan in Chunk F.

---

## Chunk D — Validation (TDD)

### D1. Red: validation tests
**Files:** `tests/test_validation.py`
**Intent:** 18 tests per spec §7 covering every §6.0 bound + rejection of webhook/lora/mode=async/unknown fields.
**Verify:** fails with ImportError on `app.validation`.

### D2. Green: GenerateRequest + resolver
**Files:** `app/validation.py`
**Intent:** Per spec §9.3. Pydantic model with `extra="forbid"`, size regex, enum checks. `resolve_and_validate(req, registry, async_mode_enabled)` post-validator that merges model defaults, enforces limits, rejects mode=async when flag off. `ValidatedJob` dataclass holding the resolved fields + `ModelConfig`.
**Verify:** `uv run pytest tests/test_validation.py -q` green.

---

## Chunk E — API endpoints (TDD)

### E1. Red: sync endpoint tests
**Files:** `tests/test_sync_endpoint.py`
**Intent:** 10 tests per spec §7 with mocked adapter + mocked S3. Uses `conftest.py`'s `client` fixture plus `app.state` overrides (monkeypatch adapter + s3 after lifespan).
**Verify:** fails on missing `app.api.images` or the new `app.state.adapter` / `app.state.s3` / `app.state.registry`.

### E2. Red: image GET tests
**Files:** `tests/test_image_get.py`
**Intent:** 6 tests per spec §7 — happy path; auth failures; 404 shapes.
**Verify:** fails for same reasons.

### E3. Green: images router + models router
**Files:** `app/api/images.py`, `app/api/models.py`
**Intent:**
- `app/api/images.py`: `POST /v1/images/generations` handler per spec §8 data flow. `GET /v1/images/{job_id}/{index_name}` gateway with `index_name` matched via regex; parses `<int>.png` or 404s.
- `app/api/models.py`: `GET /v1/models` OpenAI-compatible shape.
- Both use `Depends(require_auth)` from `app.auth`.
- Private helper `_raise_if_not_png(data: bytes)` in images.py.
**Verify:** both test modules green.

### E4. Wire in main.py
**Files:** `app/main.py`
**Intent:** Extend lifespan per spec §9.6 — load registry, instantiate + ensure S3, instantiate ComfyUIAdapter. Mount both new routers. Expose `app.state.adapter`, `app.state.s3`, `app.state.registry`, `app.state.public_base_url` normalized (rstrip("/")).
**Verify:** all prior tests still green (`test_health` still passes because lifespan survives registry + s3 init when deps are reachable; conftest may need env wiring).

### E5. Conftest updates
**Files:** `tests/conftest.py`
**Intent:** Set required env for new deps — `IMAGE_GEN_PUBLIC_BASE_URL=http://testserver`, `S3_INTERNAL_ENDPOINT=http://minio:9000` (or mock), `S3_BUCKET=image-gen`, `S3_ACCESS_KEY=minioadmin`, `S3_SECRET_KEY=minioadmin`, `COMFYUI_URL=http://comfyui:8188`, `COMFYUI_WS_URL=ws://comfyui:8188/ws`, `VRAM_BUDGET_GB=12`. Startup lifespan needs these to instantiate S3Storage + Registry + ComfyUIAdapter without reaching real services.
**Verify:** existing test_health, test_auth, test_logging, test_job_store still pass.

Tricky part: lifespan tries to `await s3.ensure_bucket()` — that reaches out to MinIO. In tests we either need to (a) point S3 at moto, (b) monkeypatch `ensure_bucket` to no-op, or (c) point S3 at an always-responding fake. Simplest: override `S3Storage.ensure_bucket` in a session-scoped conftest fixture to be a no-op. Same for `ComfyUIAdapter.__init__` — we don't want it trying to open a real WS connection. Actually our adapter only opens WS lazily on `wait_for_completion`, so instantiation is safe. S3 ensure_bucket at lifespan is the only hot spot.

Plan: conftest patches `app.storage.s3.S3Storage.ensure_bucket` to return None for tests. `test_s3_storage.py` uses the real class via moto (bypasses patch by importing from the module directly).

---

## Chunk F — Integration

### F1. Full unit suite + lint
**Verify:** `uv run pytest -q -m "not integration"` green. `uv run ruff check .`. `uv run ruff format --check .`.

### F2. Integration test against live stack
**Files:** `tests/integration/test_e2e_sync.py`
**Intent:** 2 tests:
- (a) POST minimal request → 200 + URL. Follow URL with the test client (using valid Bearer) → 200 + PNG magic bytes.
- (b) The PNG size matches the requested `1024x1024` (actually decode the PNG header to read width/height).
**Verify:** `docker compose up -d` (all services), then `uv run pytest -m integration -q`. Expected runtime ~30-60 s for the generation.

### F3. Plan verification command
```
pytest -m "not integration" -q && \
docker compose up -d && \
pytest -m integration -q tests/integration/test_e2e_sync.py && \
curl -X POST -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"noobai-xl-v1.1","prompt":"test","size":"512x512","steps":8}' \
  http://127.0.0.1:8700/v1/images/generations | jq .
```

---

## Order of execution

```
A1 → A2 → A3 → A4            # deps + env + arch (A4 can land anytime)
 ↓
B1 → B2                      # S3 storage (TDD, moto-backed)
 ↓
C1 → C2 → C3                 # registry (TDD)
 ↓
D1 → D2                      # validation (TDD)
 ↓
E1 (+E2) → E3 → E4 → E5      # API + main wiring (TDD; E5 conftest tweaks)
 ↓
F1 → F2 → F3                 # verify
```

## Commit checkpoints

Single commit at cycle end. Message template:

```
feat(cycle-3): MinIO gateway + model registry + POST /v1/images/generations

- app/storage/s3.py: single boto3 client (internal endpoint), tenacity
  retry on upload, bucket ensure idempotent, object_key_for pure helper.
- app/registry/models.py: Registry + load_registry with startup validation
  (checkpoint + vae + workflow + anchors + VRAM budget).
- config/models.yaml: noobai-xl-v1.1 entry (eps, limits + defaults).
- app/validation.py: Pydantic GenerateRequest per arch §6.0 (extra=forbid,
  enum samplers/schedulers, rejects webhook/lora/mode=async).
- app/api/images.py: POST /v1/images/generations sync handler + GET
  /v1/images/{job_id}/{index}.png gateway (Bearer auth, streams from S3).
- app/api/models.py: GET /v1/models OpenAI-compatible shape.
- app/main.py: lifespan wires registry + s3 + adapter into app.state.
- Tests: 18 validation + 8 registry + 6 storage + 10 sync + 6 gateway
  + 2 real-stack integration.
- Arch v0.6: backend gateway replaces presigned URLs (§4.6 + §6.X + §11),
  drops S3_PUBLIC_ENDPOINT and PRESIGN_TTL_S, adds IMAGE_GEN_PUBLIC_BASE_URL.
```

## Risks during BUILD

| Risk | Mitigation |
|---|---|
| boto3 blocking calls freeze tests | every S3 call via `asyncio.to_thread`; moto handles async via same path |
| Conftest lifespan needs env for S3 + ComfyUI | `os.environ.setdefault` at top of conftest; lifespan uses `.getenv` fallbacks |
| Lifespan calls `ensure_bucket` → real network in unit tests | monkeypatch `S3Storage.ensure_bucket` → no-op in conftest, real call in `test_s3_storage.py` + integration |
| Adapter init opens resources on import | verified: Cycle 2's adapter is lazy — __init__ creates httpx.AsyncClient but no WS/HTTP calls |
| Pydantic validator lookup of model name needs registry at schema-level | split into two phases: Pydantic validates shape, `resolve_and_validate` does registry lookup |
| `IMAGE_GEN_PUBLIC_BASE_URL` in prod behind ingress must be HTTPS | operator-controlled; document in .env.example |
| moto's `mock_s3` API changed between major versions | pin `moto[s3]>=5,<6`; tests use `mock_aws` / `mock_s3` with version-appropriate import |
| Integration test times out on first cold-load of checkpoint | JOB_TIMEOUT_S=300; adapter's wait_for_completion respects it |

---

*End of task plan.*
