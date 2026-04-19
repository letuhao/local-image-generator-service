# Spec — Cycle 6: Civitai fetcher (URL-driven, async, 11-rule hardened)

> **Cycle:** 6 of 11 · **Size:** L (8 core files, 6 logic areas, 1 side effect)
> **Parent plan:** [docs/plans/2026-04-18-image-gen-service-build.md §Cycle 6](../plans/2026-04-18-image-gen-service-build.md)
> **Arch refs:** §4.5 (LoRA manager + 11 rules), §6.5 (`POST /v1/loras/fetch`), §11 (security), §13 (error codes), §17 (defer multi-hash + re-verify)
> **Author:** agent (letuhao1994 approved 2026-04-19)

---

## 1. Goal (verbatim from plan)

> Admin-scope client can `POST /v1/loras/fetch` with a Civitai URL, the LoRA lands in `./loras/` with a verified SHA-256, concurrent requests for the same URL dedupe.

Done means:
- `POST /v1/loras/fetch {url}` (admin scope) returns `202 Accepted {request_id, poll_url}` immediately.
- Fetcher runs in the background. `GET /v1/loras/fetch/{request_id}` returns `{status, progress_bytes, total_bytes, error?, dest_name?}`.
- On success, the `.safetensors` lands at `./loras/civitai/<slug>_<version_id>.safetensors` with a full sidecar JSON alongside.
- `GET /v1/loras` picks up the new LoRA on the next scan (Cycle 5 scanner); `POST /v1/images/generations` can use it via `loras: [{name: "civitai/<slug>_<version_id>", weight: ...}]`.
- Concurrent fetches for the same `(model_id, version_id)` dedupe via an in-process lock; the second caller gets the same `request_id` and polls the same row.
- SHA-256 mismatch between Civitai metadata and downloaded bytes → row marked `failed` with `error_code="sha_mismatch"`, partial file deleted.
- LRU eviction triggers only if `du(./loras/) + incoming > LORA_DIR_MAX_SIZE_GB * 1e9`, protects active jobs + 7-day-used + user drops.

## 2. Decisions locked in CLARIFY

| Q | Decision |
|---|---|
| Q1 Input shape | **URL-based**. Request body: `{url: str}`. Accept both `civitai.com` and `civitai.red` page URLs; API + download calls go to `civitai.com` (shared backend). |
| Q2 URL parsing | **Strict** — require explicit `?modelVersionId=<vid>` query param OR `/api/download/models/<vid>` shape. Bare `/models/<id>` without version → `400 validation_error: version_id required; append ?modelVersionId=<id> to the URL`. |
| Q3 Sync vs async | **Always async.** Returns `202 {request_id, poll_url: "/v1/loras/fetch/<request_id>"}`. Poll endpoint returns `{status, progress_bytes, total_bytes, dest_name?, error?}`. |
| Q4 Restart recovery | **Handover.** On boot, any row in `pending\|downloading\|verifying` flips to `failed{service_restarted, handover=true}`. Partial `.safetensors.tmp` files under `./loras/civitai/` cleaned up. Caller polls → sees failure → retries. |
| Q5 SHA-256 strictness | **Strict.** Metadata missing `files[].hashes.SHA256` → refuse fetch with `validation_error: file missing SHA256 hash; cannot verify`. |
| Q6 Eviction policy | LRU by **sidecar `last_used` timestamp** (updated on every `validation.resolve_and_validate` that resolves the LoRA). Protect: (α) LoRAs referenced by non-terminal jobs' `input_json`; (β) LoRAs with `last_used > now - 7 days`; (γ) LoRAs without a sidecar (user hand drops). If even after max eviction we can't fit → `507 Insufficient Storage`. |
| Q7a Disk pre-check | `shutil.disk_usage(loras_root).free >= expected_size * 2.0`. Else `507`. |
| Q7b Retry | Metadata fetch: tenacity 3x exponential on 5xx/connection errors; 401/403/404 surface immediately. Download: 3x retry on 5xx/ReadTimeout/ConnectionError; no HTTP Range resume. 403 mid-download → no retry, surface. |
| Q8a Persistence | **New `lora_fetches` SQLite table.** Does NOT reuse `jobs`. Columns listed in §8.2. Same WAL + single-connection-with-lock pattern as `jobs`. |
| Q8b Save-path | `./loras/civitai/<slug>_<version_id>.safetensors` + sibling `.json`. `<slug>` derived from Civitai `files[].name` (strip `.safetensors` suffix, regex-sanitize to `[A-Za-z0-9_\-.]`, collapse runs of `_`). Canonical LoRA `name` is `civitai/<slug>_<version_id>` (matches Cycle 5 scanner POSIX-path convention). |

## 3. In scope (this cycle only)

- `app/loras/civitai.py` — `CivitaiFetcher` class + URL parser + metadata/download wrappers + SHA-256 streaming verify + LRU eviction + per-(model_id, version_id) asyncio.Lock + concurrent-fetch semaphore.
- `app/loras/eviction.py` — `evict_for(incoming_size, registry, store) -> int` (returns bytes reclaimed). Pure function operating on scanned sidecars + SQLite queries.
- `app/api/loras.py` (extend) — add `POST /v1/loras/fetch` (admin scope) + `GET /v1/loras/fetch/{request_id}` (admin scope).
- `app/queue/fetches.py` (new, mirrors `app/queue/jobs.py`) — `LoraFetch` dataclass + CRUD helpers: `create_pending`, `get_by_id`, `set_status`, `set_progress`, `set_dest_name`, `set_failed`, `scan_non_terminal`, `find_active_by_version`.
- `app/queue/fetches_recovery.py` — boot-time scan: non-terminal rows → `failed{service_restarted, handover=true}`; glob `./loras/civitai/**/*.tmp` → unlink.
- `migrations/003_lora_fetches.sql` — table DDL.
- `app/validation.py` (extend) — touch sidecar `last_used` on successful `resolve_and_validate` per LoRA reference. `asyncio.to_thread` wrapped.
- `app/main.py` (extend) — install `CivitaiFetcher` on `app.state.fetcher`; spawn fetcher's semaphore-gated worker task; call `fetches_recovery.recover_fetches` after image-gen recovery.
- `docker-compose.yml` (modify) — flip `./loras` service-side mount from `:ro` to writable (`./loras:/app/loras`). ComfyUI side stays `:ro`.
- `.env.example` — document `CIVITAI_API_TOKEN`, `LORA_DIR_MAX_SIZE_GB`, `LORA_MAX_SIZE_BYTES`, `LORA_MAX_CONCURRENT_FETCHES=1`, `LORA_RECENT_USE_DAYS=7`, `LORA_LAST_USED_DEBOUNCE_S=300`, `LORA_FETCH_METADATA_TIMEOUT_S=30`, `LORA_FETCH_DOWNLOAD_OVERALL_TIMEOUT_S=1800`, `LORA_FETCH_CHUNK_READ_TIMEOUT_S=30`.
- Tests: `tests/test_civitai_url_parser.py`, `tests/test_civitai_fetch.py` (respx-mocked), `tests/test_lora_eviction.py`, `tests/test_fetches_store.py`, `tests/test_fetches_endpoint.py`, `tests/test_fetches_recovery.py`. Integration: `tests/integration/test_civitai_real.py` gated on `CIVITAI_API_TOKEN` present.

## 4. Out of scope

- **Multi-hash (BLAKE3, AutoV2, CRC32)** — arch §17 deferred.
- **Sidecar re-verify on use** — arch §17 deferred.
- **Background periodic re-scan** of Civitai for model updates — defer.
- **HTTP Range resume** on partial downloads — documented follow-up.
- **Cross-restart fetch resume** — handover only (decision Q4).
- **Caller-provided save name** — server derives from Civitai metadata.
- **Delete endpoint** — `DELETE /v1/loras/{name}` is Cycle 10 or later if needed.
- **Admin-only "force refresh"** — if a LoRA at the target path already exists, fetch returns `200 done` with existing metadata (idempotent); no re-download.

## 5. File plan

| # | Path | Kind | Notes |
|---|---|---|---|
| 1 | `app/loras/civitai.py` | new | `CivitaiFetcher` + URL parser + streaming download |
| 2 | `app/loras/eviction.py` | new | LRU with 3-axis protection |
| 3 | `app/queue/fetches.py` | new | `LoraFetch` CRUD |
| 4 | `app/queue/fetches_recovery.py` | new | boot scan + tmp cleanup |
| 5 | `app/api/loras.py` | modify | +POST `/v1/loras/fetch`, +GET `/v1/loras/fetch/{id}` |
| 6 | `app/validation.py` | modify | touch sidecar `last_used` on resolve |
| 7 | `app/main.py` | modify | install fetcher + spawn worker + recovery call |
| 8 | `app/loras/scanner.py` | modify | read `last_used` from sidecar into `LoraMeta` |
| 9 | `migrations/003_lora_fetches.sql` | new | table DDL |
| 10 | `docker-compose.yml` | modify | flip service-side `./loras` to writable |
| 11 | `.env.example` | modify | document new vars |
| 12 | `tests/test_civitai_url_parser.py` | new | 12 tests |
| 13 | `tests/test_civitai_fetch.py` | new | 10 tests (respx-mocked) |
| 14 | `tests/test_lora_eviction.py` | new | 8 tests |
| 15 | `tests/test_fetches_store.py` | new | 5 tests |
| 16 | `tests/test_fetches_endpoint.py` | new | 6 tests |
| 17 | `tests/test_fetches_recovery.py` | new | 3 tests |
| 18 | `tests/integration/test_civitai_real.py` | new | 1 test, `CIVITAI_API_TOKEN`-gated |
| 19 | `tests/test_lora_scanner.py` | modify | +1 test: `last_used` round-trips |
| 20 | `tests/test_validation.py` | modify | +2 tests: sidecar debounce behavior |

## 6. Test matrix

### `test_civitai_url_parser.py` (12)
- `https://civitai.com/models/123?modelVersionId=456` → `(host="civitai.com", model_id=123, version_id=456)`
- `https://civitai.com/models/123/cool-slug?modelVersionId=456` → same
- `https://civitai.red/models/123?modelVersionId=456` → same but `host="civitai.red"`
- `https://civitai.com/api/download/models/456` → `(host="civitai.com", model_id=None, version_id=456)`
- Bare `/models/123` (no version) → `ValueError: version_id required`
- Non-Civitai host (`example.com`) → `ValueError: host not in allowlist`
- `http://civitai.com/...` (not https) → `ValueError: scheme must be https`
- `https://CIVITAI.COM/models/...` → accepted, host normalized to `civitai.com` (case-insensitive)
- `https://civitai.com.evil.com/models/...` → rejected (exact-match, not suffix)
- `https://evil.civitai.com/models/...` → rejected (exact-match)
- `https://user:pass@civitai.com/models/...` → rejected (userinfo forbidden)
- Query param casing `?ModelVersionId=456` → rejected (strict lowercase)

### `test_civitai_fetch.py` (10, respx-mocked)
- Happy path: metadata 200 → download 200 with correct SHA256 → file lands + sidecar written, status=`done`
- 401 on metadata → immediate fail with `civitai_auth` error_code
- 404 on metadata → fail `civitai_version_not_found`
- 5xx on metadata → 3 retries then `civitai_unavailable`
- Missing `files[].hashes.SHA256` → fail `validation_error` pre-download
- Downloaded size > `LORA_MAX_SIZE_BYTES` cap → fail `lora_too_large`, partial deleted
- SHA-256 mismatch post-download → fail `sha_mismatch`, partial deleted
- Non-`.safetensors` file extension in metadata → fail `validation_error`
- Duplicate concurrent fetch of same `(model_id, version_id)` → second caller gets same `request_id`
- Idempotent: destination `.safetensors` already on disk → fetch short-circuits `status=done`, no network

### `test_lora_eviction.py` (8)
- `du < max` → no eviction, returns 0 bytes reclaimed
- One stale LoRA with `last_used` 10 days ago → evicted
- Stale LoRA used within 7 days → protected
- Stale LoRA referenced in a `queued` or `running` job → protected even if `last_used` is old
- Stale LoRA without a sidecar → protected (user drop)
- Even after max eviction we can't fit → raises `InsufficientStorageError`
- Eviction removes both `.safetensors` AND `.json` sidecar atomically
- TOCTOU: candidate selected for eviction, new job enqueued between selection
  and delete referencing that candidate → recheck skips it and evicts the
  next-oldest instead

### `test_fetches_store.py` (5)
- `create_pending` → row with `status='pending'`
- `set_status` valid transitions (pending→downloading→verifying→done)
- Invalid transition (done → pending) → `InvalidTransitionError`
- `scan_non_terminal` returns only non-terminal rows
- `find_active_by_version(model_id, version_id)` returns in-flight row or None

### `test_fetches_endpoint.py` (6)
- POST without admin key → 403
- POST with generation key → 403 (admin only)
- POST with bad URL → 400 `validation_error`
- POST happy path → 202 with `{request_id, poll_url}`
- GET non-existent id → 404
- GET existing id → status shape

### `test_fetches_recovery.py` (3)
- Non-terminal rows flipped to failed on startup
- `.tmp` files under `./loras/civitai/` cleaned up
- Terminal rows untouched

### `tests/integration/test_civitai_real.py` (1, opt-in)
- Gated on `CIVITAI_API_TOKEN` present (pytest.skip otherwise)
- Fetches a known-small public LoRA (user picks + documents in spec or env)
- Asserts file lands + sidecar written + SHA matches

### `tests/test_lora_scanner.py` (+1)
- Sidecar with `last_used: "2026-04-19T12:00:00Z"` → `LoraMeta.last_used` round-trips.

### `tests/test_validation.py` (+2, debounce)
- LoRA resolve where sidecar's `last_used` is 10 min ago → sidecar rewritten.
- LoRA resolve where sidecar's `last_used` is 2 min ago (< 5 min debounce) → sidecar untouched; `mtime` stable across the call.

## 7. Data flow — fetch request lifecycle

```
POST /v1/loras/fetch  {url: "https://civitai.com/models/123?modelVersionId=456"}  Bearer admin…
  │
  ▼
api.loras_fetch handler:
  1. require_admin (already enforced by dep)
  2. parse_civitai_url(url) → (host, model_id=123, version_id=456)
  3. fetches.find_active_by_version(123, 456) → dedupe; if present, return existing {request_id, poll_url}
  4. fetches.create_pending(url, model_id, version_id) → request_id
  5. app.state.fetcher.enqueue(request_id)   # semaphore-gated (LORA_MAX_CONCURRENT_FETCHES=1)
  6. return 202 {request_id, poll_url: "/v1/loras/fetch/<request_id>"}
  │
  ▼
fetcher worker (per-version lock + semaphore):
  1. acquire asyncio.Lock[(model_id, version_id)]
  2. set_status(request_id, "downloading")
  3. GET https://civitai.com/api/v1/model-versions/456
     Authorization: Bearer $CIVITAI_API_TOKEN
     tenacity 3x on 5xx/connect error
     → parse .files[primary=true]: {name, sizeKB, hashes: {SHA256}, downloadUrl}
  4. pre-download checks:
     - hashes.SHA256 present → else fail validation_error
     - file name endswith(".safetensors") → else fail validation_error
     - sizeKB*1024 <= LORA_MAX_SIZE_BYTES → else fail lora_too_large
     - shutil.disk_usage(loras_root).free >= expected_size*2.0 → else run evict_for(expected_size, ...)
       if still can't fit → fail 507 insufficient_storage
  5. derive save_path:
     - slug = sanitize(files[0].name.removesuffix(".safetensors"))
     - canonical_name = f"civitai/{slug}_{version_id}"
     - dest = loras_root / "civitai" / f"{slug}_{version_id}.safetensors"
     - tmp = dest.with_suffix(".safetensors.tmp")
  6. idempotency: if dest.is_file():
     - if sibling .json sidecar missing → still fetch metadata + write sidecar
       (retroactive metadata attach for a hand-dropped file that happens to
       land at the same path); no re-download of bytes.
     - short-circuit set_status("done"), skip download
  7. streaming download:
     - GET downloadUrl with follow_redirects, httpx.Timeout(
         connect=10, read=LORA_FETCH_CHUNK_READ_TIMEOUT_S=30, write=30, pool=5)
       Per-chunk read timeout is 30s — a stuck connection fails fast instead
       of hogging the sole fetcher slot for the full 30-minute overall window.
     - write to tmp in 1 MiB chunks; update streaming SHA256; update set_progress every 4 MiB
     - overall deadline LORA_FETCH_DOWNLOAD_OVERALL_TIMEOUT_S=1800 enforced via
       asyncio.timeout() wrapper around the streaming download block.
     - set_status(request_id, "verifying")
     - finalize hash.hexdigest() vs metadata.hashes.SHA256
       mismatch → unlink(tmp); fail sha_mismatch
  8. write sidecar:
     {
       "name": canonical_name,
       "filename": f"civitai/{slug}_{version_id}.safetensors",
       "sha256": "<hex>",
       "source": "civitai",
       "civitai_model_id": 123,
       "civitai_version_id": 456,
       "base_model_hint": metadata.baseModel,
       "trigger_words": metadata.trainedWords or [],
       "fetched_at": "<now ISO-8601>",
       "last_used": null
     }
     written atomically (tmp + rename)
  9. os.rename(tmp, dest)  # atomic on same filesystem
 10. set_dest_name(request_id, canonical_name); set_status("done")
 11. audit log: lora.fetch.ok {request_id, url, canonical_name, size_bytes, duration_ms}
 12. release lock
  │
  ▼
GET /v1/loras/fetch/<request_id>  Bearer admin…
  │
  ▼
  returns row: {status, progress_bytes, total_bytes, dest_name?, error_code?, error_message?}
```

## 8. Concrete API contracts

### 8.1 `app/loras/civitai.py`

```python
_ALLOWED_HOSTS: frozenset[str] = frozenset({"civitai.com", "civitai.red"})
# API + download calls go to civitai.com regardless of page host.
_API_HOST = "civitai.com"

@dataclass(frozen=True, slots=True)
class ParsedCivitaiUrl:
    host: str                # "civitai.com" | "civitai.red" (always lowercased)
    model_id: int | None     # None on /api/download/models/<vid> shape
    version_id: int          # required

def parse_civitai_url(url: str) -> ParsedCivitaiUrl:
    """Strict parser. Raises ValueError on any malformed/ambiguous input.

    Defenses against host-impersonation:
    - Requires `https://` scheme (no http, no file, no ftp).
    - Compares `urlparse(url).hostname.lower()` against `_ALLOWED_HOSTS`
      exactly — no startswith/endswith. Rejects `civitai.com.evil.com`,
      `CIVITAI.COM@evil.com` (userinfo trick), `evil.civitai.com`, etc.
    - Rejects URLs with non-empty userinfo (`user:pass@`) or non-empty port.
    """

class CivitaiFetcher:
    def __init__(
        self,
        *,
        store: JobStore,
        loras_root: Path,
        registry: Registry,
        api_token: str | None,
        http_client: httpx.AsyncClient,
        dir_max_bytes: int,
        file_max_bytes: int,
        recent_use_days: int,
        max_concurrent: int = 1,
    ) -> None: ...

    async def enqueue(self, request_id: str) -> None:
        """Spawn a background task that performs fetch under semaphore + per-version lock."""

    async def close(self) -> None:
        """Cancel outstanding fetches on shutdown."""
```

### 8.2 `migrations/003_lora_fetches.sql`

```sql
CREATE TABLE IF NOT EXISTS lora_fetches (
    id TEXT PRIMARY KEY,                                -- ksuid
    url TEXT NOT NULL,
    civitai_model_id INTEGER,                           -- may be NULL for /api/download/ URLs
    civitai_version_id INTEGER NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('pending','downloading','verifying','done','failed')),
    progress_bytes INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER,                                -- NULL until metadata fetched
    dest_name TEXT,                                     -- canonical_name on done
    error_code TEXT,
    error_message TEXT,
    handover INTEGER NOT NULL DEFAULT 0,                -- boolean; set on recovery
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Dedupe at the DB layer: only ONE non-terminal row per version_id allowed.
-- Handler races (both requests see find_active_by_version() → None) get caught
-- here; loser retries find_active_by_version and returns the winner's request_id.
CREATE UNIQUE INDEX IF NOT EXISTS uq_lora_fetches_active_version
    ON lora_fetches(civitai_version_id)
    WHERE status IN ('pending','downloading','verifying');
CREATE INDEX IF NOT EXISTS idx_lora_fetches_status
    ON lora_fetches(status, updated_at);
```

### 8.3 `app/api/loras.py` additions

```python
class CivitaiFetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=512)

@router.post("/v1/loras/fetch", status_code=202)
async def post_fetch(
    request: Request,
    body: CivitaiFetchRequest,
    kid: str = Depends(require_admin),
) -> dict:
    """Schedule a Civitai fetch. Returns 202 with poll URL. Idempotent on
    concurrent requests for the same (model_id, version_id).

    Dedupe flow:
      1. parse URL → version_id.
      2. SELECT non-terminal row by version_id; if found, return its request_id.
      3. INSERT new pending row; on IntegrityError (uq_lora_fetches_active_version
         tripped by a concurrent sibling), goto 2 — the winner is now committed,
         loser fetches and returns its request_id.
    """

@router.get("/v1/loras/fetch/{request_id}")
async def get_fetch_status(
    request: Request,
    request_id: str,
    kid: str = Depends(require_admin),
) -> dict:
    """Returns {id, status, progress_bytes, total_bytes, dest_name?, error_code?, error_message?}."""
```

### 8.4 Error codes

**Sync-return from `POST /v1/loras/fetch` (caller sees HTTP response):**

| Condition | HTTP | `error.code` |
|---|---|---|
| Bad URL shape / missing version_id / wrong host / wrong scheme | 400 | `validation_error` |
| Body fails Pydantic (missing `url`, too long, etc.) | 400 | `validation_error` |
| No Bearer | 401 | `auth_error` |
| Bearer present but not admin scope | 403 | `admin_required` |
| Valid request, queued successfully | 202 | (no error body) |

**Sync-return from `GET /v1/loras/fetch/{request_id}`:**

| Condition | HTTP | `error.code` |
|---|---|---|
| Unknown `request_id` | 404 | `not_found` |
| No / non-admin Bearer | 401 / 403 | `auth_error` / `admin_required` |
| Known id, returns row state | 200 | (no error; shape in §8.3) |

**Job-row `error_code` values (visible via `GET /v1/loras/fetch/{id}` when `status=failed`):**

| Condition | `error_code` |
|---|---|
| Civitai API returned 401/403 on metadata | `civitai_auth` |
| Civitai API returned 403 mid-download (token expired) | `civitai_auth` |
| Civitai returned 404 on metadata | `civitai_version_not_found` |
| Civitai 5xx after 3 retries | `civitai_unavailable` |
| Metadata missing `files[].hashes.SHA256` | `validation_error` |
| File name from metadata not ending `.safetensors` | `validation_error` |
| Download or `sizeKB*1024` exceeds `LORA_MAX_SIZE_BYTES` | `lora_too_large` |
| Downloaded bytes SHA256 ≠ metadata SHA256 | `sha_mismatch` |
| Disk space insufficient after max eviction | `insufficient_storage` |
| Service restart mid-fetch (handover) | `service_restarted` |

**Note:** 507 is NOT a sync HTTP status in this design — the handler always returns 202 on a valid body. Disk-space insufficiency surfaces as a job-row `error_code="insufficient_storage"` visible via the poll endpoint.

### 8.5 Eviction algorithm (`app/loras/eviction.py`)

```python
class InsufficientStorageError(Exception): ...

def evict_for(
    *,
    incoming_size: int,
    loras_root: Path,
    store: JobStore,                # sync proxy or async-to-thread wrapper
    dir_max_bytes: int,
    recent_use_days: int,
) -> int:
    """Delete stale civitai-fetched LoRAs to make room. Returns bytes reclaimed.

    Scans sidecars under loras_root/civitai/**; skips:
      α) LoRAs referenced by non-terminal jobs' input_json
      β) LoRAs with sidecar.last_used > now - recent_use_days
      γ) LoRAs without a sidecar (user drops — though under civitai/ this is
         unlikely; scanner may surface edge cases)

    Orders candidates by sidecar.last_used ascending (oldest first).
    Deletes .safetensors + .json pairs. Raises InsufficientStorageError
    if after max eviction we still can't fit incoming_size.

    TOCTOU safeguard: after candidate selection, for each candidate immediately
    before unlink(), re-query `input_json LIKE '%<name>%'` in non-terminal jobs
    under a single SQLite transaction. If a new job enqueued between selection
    and delete references this candidate, skip it and continue with the next
    oldest. Prevents delete-out-from-under a freshly-enqueued generation.
    """
```

### 8.6 Sidecar `last_used` touch (`app/validation.py`)

```python
# In resolve_and_validate, after the existing realpath + is_file checks:
for spec in req.loras:
    target = ...
    await _touch_last_used(target.with_suffix(".json"))
```

The touch is a best-effort operation:
- Wrapped in `asyncio.to_thread`.
- **Debounced:** reads sidecar first; if existing `last_used` is within the last
  `LORA_LAST_USED_DEBOUNCE_S=300` (5 min), skip the write. Reduces write
  amplification from O(loras_per_request × request_rate) to
  O(unique_loras_per_5min). Fetcher's freshly-written sidecars always have
  `last_used: null` so the first generation referencing them always touches.
- Reads sidecar → mutates `last_used` → writes back atomically (tmp + rename).
- If sidecar doesn't exist (user drop), skip silently (γ-protection at the data layer).
- If write fails (disk full, race), log at WARNING and proceed — don't block the request on an observability update.

### 8.7 Concurrency model

- One **process-wide asyncio.Semaphore** (`LORA_MAX_CONCURRENT_FETCHES=1`) gates the actual download + verify work.
- Per-`(model_id, version_id)` **asyncio.Lock** dict ensures duplicate concurrent POSTs serialize; the second caller's handler sees the same in-flight row and returns its `request_id`.
- Eviction runs **inside** the semaphore-gated fetch task, so there's at most one evictor at a time.

### 8.8 Compose change

```yaml
image-gen-service:
  volumes:
    # Cycle 5 was :ro; Cycle 6 needs writable for fetched downloads + sidecar updates.
    - ./loras:/app/loras
```
Compose remount policy: no restart required on the `:ro → rw` change when developer rebuilds the service container; the user recreates it via `docker compose up -d --build image-gen-service`.

## 9. Risks + mitigations

| Risk | Mitigation |
|---|---|
| SHA-256 mismatch after 30-minute download | Expected failure mode; partial file cleaned up, caller retries |
| Civitai returns 403 on NSFW without proper token | Strict API behavior; user sets `CIVITAI_API_TOKEN`; if token is insufficient for a given NSFW asset, fail clearly |
| Two containers (service + test harness) both try to write `./loras/civitai/` | Not our target — single service instance. Documented assumption. |
| Sidecar `last_used` write contention during high generation throughput | Best-effort + to_thread + WARN-and-continue on write failure |
| LRU eviction deletes a LoRA the user just picked but hasn't used yet | Eviction only touches `./loras/civitai/`, never root; user drops are γ-protected |
| Partial `.tmp` file left over after crash | Recovery path globs + unlinks on boot |
| Civitai API schema drift | Parse defensively: `files = resp.get("files") or []`, check `primary=true`, require `hashes.SHA256` — unknown shape → validation_error with the Civitai response echoed in logs |
| Concurrent POST for the same (model_id, version_id) without lock → two downloads | `find_active_by_version` at handler entry + asyncio.Lock keyed by version_id |
| Admin pastes a `.red` page URL expecting NSFW content to download | Same backend; works transparently. Document in `.env.example`. |
| `sizeKB` from metadata drifts from actual download size | Use streaming + running-size check against `LORA_MAX_SIZE_BYTES` during download; abort if exceeded mid-stream |
| ComfyUI can't find a freshly fetched LoRA because its models list is stale | ComfyUI ≥0.9 asset scanner detects new files on next request (verified during Cycle 5 fix commit `bc3edac`) — if stale, `/prompt` will return a node error the caller can see |

## 10. Self-review checklist

- [x] All 8 CLARIFY decisions reflected in §2 + §8
- [x] Every file in §5 has coverage in §6
- [x] No TBD/TODO/placeholders
- [x] Descope scan: no multi-hash, no Range-resume, no re-verify-on-use, no delete endpoint
- [x] Cycle 5 γ-protection inherited for eviction ("no sidecar = user drop")
- [x] Idempotent behavior for re-fetching an existing file (no redundant network)
- [x] Dedup via find_active_by_version + asyncio.Lock
- [x] Recovery path handles tmp cleanup + non-terminal row flipping
- [x] New `lora_fetches` table documented with WAL + index
- [x] Compose rw flip documented (service side only; ComfyUI stays :ro)
- [x] Audit log line (rule #11) is the `lora.fetch.ok` INFO on success + `lora.fetch.failed` WARN on failure — both with `request_id` context

---

*End of spec.*
