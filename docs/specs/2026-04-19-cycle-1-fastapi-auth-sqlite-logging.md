# Spec — Cycle 1: FastAPI auth + SQLite job store + structured JSON logging

> **Cycle:** 1 of 11 · **Size:** XL (12 files, 7 logic changes, 1 side effect)
> **Parent plan:** [docs/plans/2026-04-18-image-gen-service-build.md §Cycle 1](../plans/2026-04-18-image-gen-service-build.md)
> **Arch refs:** §4.1 (gateway), §4.2 (job queue & store), §5 (env), §11 (auth + security), §13 (observability)
> **Author:** agent (letuhao1994 approved 2026-04-19)

---

## 1. Goal (verbatim from plan)

> Every request through our service is authenticated, every job is persistable, every log line is structured JSON with correlation id. Still no image generation.

Done means:
- Bearer auth enforced on `/health` verbose shape + on every non-`/health` endpoint registered in this cycle.
- SQLite schema in place and a Job CRUD surface covering the status transitions used by Cycles 3–9.
- `/health` probes the DB and flips to `503` when unreachable.
- Every log line is JSON with `request_id` (per-request) and `job_id` (when applicable).

## 2. In scope (this cycle only)

- `app/auth.py` — multi-key parser, kid derivation, `hmac.compare_digest`, FastAPI `Depends` helpers.
- `app/logging_config.py` — structlog processor chain + stdlib bridge + prompt-redaction processor (flat module, no new package).
- `app/middleware/logging.py` — `RequestContextMiddleware` that binds `request_id` (from inbound `X-Request-Id` if present + length-capped, else a fresh UUID4) and emits a structured access line. `key_id` is bound separately by the auth dependency, not here.
- `app/queue/store.py` — `aiosqlite` connection lifecycle, migration runner, write-lock helper.
- `app/queue/jobs.py` — `Job` dataclass + CRUD helpers (`create_queued`, `set_running`, `set_completed`, `set_failed`, `get_by_id`) + `abandoned` transition (unused until Cycle 4 but schema-exposed).
- `app/api/health.py` — `/health` handler with DB probe + auth-gated verbose shape.
- `app/main.py` — register middleware + lifespan that opens/closes the DB; mount the new router.
- `migrations/001_init.sql` — full jobs schema from arch §4.2 (all columns, including webhook columns even though they're unused until Cycle 8/9).
- `config/` — `logging.py` helper module (preferred over `logging.ini` so structlog + stdlib share one processor chain).
- `tests/test_auth.py`, `tests/test_job_store.py`, `tests/test_health.py` (update), `tests/test_logging.py`.
- Pyproject additions: `aiosqlite>=0.20,<0.21`, `structlog>=24.4,<25`, `svix-ksuid>=0.6.2,<0.7` (needed now because arch §4.2 mandates `id` format `gen_<ksuid>` and we generate ids in `create_queued` this cycle). Update the forward-compat comment in `pyproject.toml` to remove `svix-ksuid` from the Cycle 6 line.
- `.env.example` additions: `LOG_PROMPTS=false`, `DATABASE_PATH=/app/data/jobs.db`.
- `docker-compose.yml` update: bind `./data:/app/data` (service-writable).
- `.gitignore` update: `data/*.db*`.

## 3. Out of scope (explicit descope)

- No real generation endpoint (`/v1/images/generations`) — Cycle 3.
- No `/v1/models`, `/v1/loras`, `/admin/reload` — later cycles.
- No ComfyUI health probe inside `/health` — Cycle 2 adds the adapter, that's when ComfyUI becomes part of the deep probe.
- No MinIO probe inside `/health` — Cycle 3 adds `app/storage/s3.py`.
- No webhook delivery machinery — Cycle 9. The **columns** ship now because migrating them in later is more painful than letting them sit nullable.
- No queue worker, no reaper, no restart recovery — Cycle 4.
- No audit log stream (`audit.jsonl`) — it belongs in Cycle 6 (first audit events come from Civitai fetch + admin calls). Cycle 1 logs auth events to the regular stream.
- No rate limiting, no metrics endpoint.

## 4. Key decisions (CLARIFY outcomes)

### 4.1 Logging stack: `structlog` + stdlib bridge

- `structlog>=24.4` added as runtime dep.
- Single processor chain:
  `merge_contextvars → add_log_level → TimeStamper(fmt="iso", utc=True) → StackInfoRenderer → format_exc_info → JSONRenderer`.
- `structlog.contextvars.bind_contextvars(request_id=..., key_id=...)` in the request middleware; `bind_contextvars(job_id=...)` inside the Job CRUD helpers on status transitions.
- Stdlib `logging` is configured with `structlog.stdlib.ProcessorFormatter` so FastAPI / uvicorn / aiosqlite logs render as JSON too.
- Log destinations: stdout only. No file handlers in this cycle.
- Level: `INFO` default, controlled by `LOG_LEVEL` env (default `INFO`). `DEBUG` required to emit prompts (see 4.4).

### 4.2 SQLite concurrency posture

- Single app-owned `aiosqlite.Connection`, lifetime = FastAPI lifespan.
- On open: `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA busy_timeout=5000`, `PRAGMA foreign_keys=ON`.
- **Write lock:** a module-level `asyncio.Lock` wraps every INSERT/UPDATE path. Reads are lock-free (WAL allows concurrent readers). This is conservative; Cycle 4 may split it if contention shows up.
- Migrations are applied on startup by reading every `.sql` under `migrations/` sorted by filename and running them inside a transaction if a `schema_version` table entry for that filename is missing. Idempotent across restarts.
- All queries use parameterised statements (`?` placeholders) — ruff `S608` stays enforced.

### 4.3 DB file location

- Host: `./data/jobs.db` (bind mount, gitignored).
- Container: `/app/data/jobs.db` (env `DATABASE_PATH`).
- `docker-compose.yml` gains `./data:/app/data` on the `image-gen-service` service.
- Prod-posture reminder in `.env.example`: operators should replace the bind with a named volume in prod.

### 4.4 Prompt logging

- `LOG_PROMPTS=false` default in `.env.example`.
- Log-emitting code paths pass `prompt=<str>` into the event dict; a dedicated processor (`_redact_prompt`) replaces the value with `"<redacted>"` unless **both** `LOG_PROMPTS=true` AND effective level is `DEBUG`.
- Same processor handles `negative_prompt`, `presigned_url`, `webhook.url` (dropped outright), and `Authorization` headers if they slip in.
- Presigned URLs + `X-Amz-Signature` are **never** rendered regardless of flag (hard drop, not conditional).

### 4.5 Auth model

- Keys read from `API_KEYS` (generation scope) + `ADMIN_API_KEYS` (admin scope) at startup. Both are comma-separated. Whitespace trimmed. Empty entries ignored.
- Each key is fingerprinted as `kid = sha256(key)[:8]` — the kid is what goes into logs and the audit trail, never the key itself.
- Comparison is `hmac.compare_digest` against each allowed key; first match wins. O(n) over a set that's typically ≤ 4 keys, so acceptable.
- Fail-closed: if `API_KEYS` is empty, **every** request to a generation-scope endpoint returns `401 {error_code: auth_error}` — the service runs but accepts nothing. Same for `ADMIN_API_KEYS` on admin endpoints.
- Two FastAPI dependencies ship:
  - `require_auth` — accepts either generation or admin key.
  - `require_admin` — admin key only; a generation key returns `403 {error_code: auth_error}`.
- Auth middleware does not run on `/health` (`/health` handles the auth check inline so unauthenticated callers still get the boolean-only shape, per arch §11).

## 5. File plan (final list)

| # | Path | Kind | Notes |
|---|---|---|---|
| 1 | `pyproject.toml` | modify | add `aiosqlite`, `structlog`, `svix-ksuid`; update forward-compat comment |
| 2 | `app/auth.py` | new | key parsing, `kid`, `require_auth`, `require_admin` |
| 3 | `app/logging_config.py` | new | structlog processor chain + stdlib bridge + redaction processor (flat, not a package) |
| 4 | `app/middleware/__init__.py` | new | empty package marker |
| 5 | `app/middleware/logging.py` | new | `RequestContextMiddleware` — bind `request_id`, log access line |
| 6 | `app/queue/__init__.py` | new | empty package marker |
| 7 | `app/queue/store.py` | new | connection lifecycle, migration runner, write lock |
| 8 | `app/queue/jobs.py` | new | `Job` dataclass + CRUD |
| 9 | `app/api/__init__.py` | new | empty package marker |
| 10 | `app/api/health.py` | new | `/health` router with DB probe + auth-gated verbose shape |
| 11 | `app/main.py` | modify | remove inline `/health`, mount router, add lifespan + middleware |
| 12 | `migrations/001_init.sql` | new | full jobs schema + `schema_version` tracking table |
| 13 | `.env.example` | modify | add `LOG_LEVEL`, `LOG_PROMPTS`, `DATABASE_PATH` |
| 14 | `docker-compose.yml` | modify | add `./data:/app/data` volume |
| 15 | `.gitignore` | modify | add `data/*.db*` |
| 16 | `tests/test_auth.py` | new | key parsing, kid derivation, 401/403 matrix |
| 17 | `tests/test_job_store.py` | new | round-trip create/read/update, status transitions, concurrent writes serialise |
| 18 | `tests/test_logging.py` | new | JSON shape, request_id propagation, redaction matrix |
| 19 | `tests/test_health.py` | modify | DB probe, 503 when DB unreachable, boolean vs verbose shape |
| 20 | `tests/conftest.py` | modify | fixture for a temp SQLite path per test; set required env before `app.main` import |

> Files 4, 6, 9 are package markers. Plan counted 12 meaningful files + 1 side effect (the new `data/` volume). Tests, markers, and config tweaks don't change the XL classification.

## 6. Test matrix (acceptance, not implementation detail)

### tests/test_auth.py
- `parse_keys` splits, trims, dedupes, ignores empties.
- `kid_for(key)` returns 8 lowercase hex chars == `sha256(key)[:8]`.
- `require_auth`: missing header → 401 `auth_error`; wrong key → 401 `auth_error`; generation key on generation route → 200; admin key on generation route → 200.
- `require_admin`: generation key → 403 `auth_error`; admin key → 200; missing → 401.
- Constant-time path: timing-channel-free by construction (use `hmac.compare_digest` — assert the call, not the timing).
- Empty `API_KEYS` → service still boots; any request to `/health` with an `Authorization` header returns 401 (the boolean-only `/health` without header still works — that's the only auth-reachable route this cycle).

### tests/test_job_store.py
- `create_queued` inserts all §4.2 columns with correct defaults (`response_delivered=0`, etc.).
- `get_by_id` returns `None` for unknown id, `Job` for known.
- `set_running` flips `queued → running`, writes `prompt_id`, updates `updated_at`.
- `set_completed` flips `running → completed`, writes `output_keys` (JSON array), `result_json`.
- `set_failed` flips any non-terminal status → `failed`, writes `error_code` + `error_message`.
- Illegal transition (`completed → running`) raises `InvalidTransition` and leaves the row untouched.
- `create_queued` 50 times concurrently — all 50 rows land, no duplicate ids (ksuid monotonic).
- `set_running` + `set_completed` on the same id from two tasks — second one sees the first's committed state (write lock serialises).
- WAL file present after first write; `journal_mode` query returns `wal`.

### tests/test_logging.py
- Emitting a log from inside a request writes a JSON line with `request_id`, `level`, `timestamp`, `event`.
- `request_id` is the UUID4 generated by middleware, or the inbound `X-Request-Id` header value (length-capped) if present.
- Job CRUD log lines carry `job_id` in addition to `request_id`.
- `LOG_PROMPTS=false` + event carrying `prompt=...` → rendered as `"prompt": "<redacted>"`.
- `LOG_PROMPTS=true` + level `INFO` → still `"<redacted>"` (DEBUG required).
- `LOG_PROMPTS=true` + level `DEBUG` → renders the real string.
- `presigned_url` and `Authorization` keys are always dropped, regardless of `LOG_PROMPTS`.

### tests/test_health.py (updated)
- Unauthenticated GET → 200, body `{"status":"ok"}` (boolean-only shape).
- Authenticated GET with generation key → 200, body includes `db: "ok"`.
- Authenticated GET with admin key → 200, same verbose shape.
- Point `DATABASE_PATH` to an unreadable path, re-create app → verbose health returns `503 {db: "unreachable"}`, unauthenticated returns `503 {"status":"degraded"}`.
- HEAD still 200.
- POST still 405.

### Manual smoke (documented, not automated)
```bash
docker compose up -d --build
curl -sf http://127.0.0.1:8700/health | jq .                              # boolean-only
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/health | jq .  # verbose
```

## 7. Schema (migration `001_init.sql`)

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    filename   TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id                         TEXT PRIMARY KEY,
    model_name                 TEXT NOT NULL,
    input_json                 TEXT NOT NULL,
    mode                       TEXT NOT NULL CHECK (mode IN ('sync','async')),
    status                     TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed','abandoned')),
    result_json                TEXT,
    error_code                 TEXT,
    error_message              TEXT,
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL,
    client_id                  TEXT,                    -- NULL until ComfyUI submit (set in set_running alongside prompt_id)
    prompt_id                  TEXT,
    output_keys                TEXT,
    response_delivered         INTEGER NOT NULL DEFAULT 0 CHECK (response_delivered IN (0,1)),
    initial_response_delivered INTEGER NOT NULL DEFAULT 0 CHECK (initial_response_delivered IN (0,1)),
    webhook_url                TEXT,
    webhook_headers_json       TEXT,
    webhook_delivery_status    TEXT CHECK (webhook_delivery_status IN ('pending','succeeded','failed','suppressed') OR webhook_delivery_status IS NULL),
    webhook_handover           INTEGER NOT NULL DEFAULT 0 CHECK (webhook_handover IN (0,1))
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_updated ON jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at     ON jobs(created_at);
```

Notes:
- `error_code` is **not** CHECK-constrained — the enum is documented in arch §13 but new codes get added across cycles; enforcing via CHECK would make future migrations painful. Validated in application code instead.
- `response_delivered` etc. are `INTEGER` with `CHECK IN (0,1)` rather than `BOOLEAN` because SQLite stores booleans as integers anyway; explicit CHECK avoids silent typos.
- The two indexes cover the two most frequent queries we already know about: the Cycle 4 orphan reaper (`status = 'completed' AND updated_at < cutoff`) and the Cycle 4 boot scanner (`status IN ('queued','running')`).

## 8. Env var additions (`.env.example`)

```
# ── Logging (Cycle 1) ────────────────────────────────────────────────────────
LOG_LEVEL=INFO                              # DEBUG | INFO | WARNING | ERROR
LOG_PROMPTS=false                           # must also be at DEBUG level to actually render prompts

# ── Storage: SQLite job store (Cycle 1) ──────────────────────────────────────
DATABASE_PATH=/app/data/jobs.db             # inside container; bind-mounted from ./data on host
```

## 9. Risks + mitigations

| Risk | Mitigation |
|---|---|
| structlog + uvicorn double-logs access lines (uvicorn emits its own) | Configure `uvicorn.access` logger to use the same JSON formatter; disable the uvicorn default text access log |
| `hmac.compare_digest` against a growing list leaks length | Not a real risk for ≤ 4 keys; if we ever support > 100 keys, switch to set membership after fingerprint hashing |
| `aiosqlite` single-connection becomes a bottleneck once the queue worker is busy | Cycle 4 has latitude to split reader/writer connections; the write-lock abstraction means call sites don't need to change |
| Migration runner silently skips a file if filename order is wrong | Filenames enforced to `NNN_<name>.sql`; loader asserts strictly ascending numeric prefix before running |
| WAL file (`*.db-wal`, `*.db-shm`) leaks into git | `.gitignore` pattern `data/*.db*` covers wal + shm |
| Tests mutate global structlog config and bleed across tests | `conftest` resets structlog state per test via a fixture that re-calls the configure function |
| Windows dev target — `./data` permissions on bind mount | Docker Desktop handles this on NTFS; confirmed working for `./models` and `./loras` mounts already; no action |

## 10. Open items — none

All four CLARIFY items from HANDOFF resolved (4.1–4.4). No new unknowns surfaced by context read.

## 11. Self-review checklist

- [x] No placeholders, no TBDs, no "add error handling here"
- [x] Every file in §5 has a stated purpose + test coverage in §6
- [x] Every arch-§13 field either has a test in §6 or is explicitly deferred in §3
- [x] Scope creep scanned: no Cycle 2+ leakage (webhook columns ship as nullable, not as behaviour)
- [x] Contradictions vs arch scanned: none; schema matches §4.2 column-for-column
- [x] Risks table is specific, not generic
- [x] Verification command from plan still executes unchanged after Cycle 1

---

## 12. Design — concrete API contracts

### 12.1 `app/auth.py`

```python
# Module-level state, initialised on first access from env.
class _Keyset:
    generation: frozenset[str]   # lowercase-trimmed keys
    admin:      frozenset[str]

def kid_for(key: str) -> str:
    """Return sha256(key)[:8] in lowercase hex. Used in logs + audit."""

def parse_keys(raw: str) -> frozenset[str]:
    """Split on comma, strip, drop empties, return frozenset."""

# FastAPI dependencies (raise HTTPException(401|403) with {error_code: "auth_error"}).
# On success, the dep also calls structlog.contextvars.bind_contextvars(key_id=kid)
# so every subsequent log line in the request carries key_id without route-handler wiring.
async def require_auth(
    authorization: str | None = Header(default=None),
) -> str:  # returns the matched kid
    ...

async def require_admin(
    authorization: str | None = Header(default=None),
) -> str:  # returns the matched admin kid
    ...

# Exceptions
class AuthError(HTTPException): ...  # 401, error_code=auth_error
class AuthScopeError(HTTPException): ...  # 403, error_code=auth_error
```

**Error body shape** (both 401 and 403):
```json
{ "error": { "code": "auth_error", "message": "<human-readable>" } }
```

### 12.2 `app/queue/store.py`

```python
class JobStore:
    def __init__(self, database_path: str) -> None: ...
    async def connect(self) -> None:
        """Open aiosqlite connection; apply pragmas; run pending migrations."""
    async def close(self) -> None: ...
    @asynccontextmanager
    async def write(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire the write lock + yield the connection; commit on exit."""
    async def read(self) -> aiosqlite.Connection:
        """Lock-free read handle (WAL safe)."""
    async def healthcheck(self) -> bool:
        """Return True if `SELECT 1` succeeds within busy_timeout."""

async def apply_migrations(conn: aiosqlite.Connection, migrations_dir: Path) -> list[str]:
    """Return list of migration filenames applied this call (empty if up-to-date)."""
```

Pragmas set on `connect()`:
```
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;
```

### 12.3 `app/queue/jobs.py`

```python
@dataclass(frozen=True, slots=True)
class Job:
    id: str                     # "gen_<ksuid>"
    model_name: str
    input_json: str             # JSON-serialised request payload
    mode: Literal["sync", "async"]
    status: Literal["queued","running","completed","failed","abandoned"]
    result_json: str | None
    error_code: str | None
    error_message: str | None
    created_at: str             # ISO-8601 UTC
    updated_at: str
    client_id: str | None       # set by set_running when ComfyUI submit happens (Cycle 2+)
    prompt_id: str | None
    output_keys: list[str]      # decoded from JSON in the row
    response_delivered: bool
    initial_response_delivered: bool
    webhook_url: str | None
    webhook_headers: dict[str, str] | None
    webhook_delivery_status: Literal["pending","succeeded","failed","suppressed"] | None
    webhook_handover: bool

# Errors
class InvalidTransition(Exception): ...
class JobNotFound(Exception): ...

# CRUD — every function takes JobStore, returns Job or raises.
# set_running/set_completed/set_failed raise JobNotFound if id is unknown.
# Illegal transitions raise InvalidTransition. get_by_id never raises — returns None.
async def create_queued(
    store: JobStore,
    *,
    model_name: str,
    input_json: str,
    mode: Literal["sync","async"] = "sync",
    webhook_url: str | None = None,
    webhook_headers: dict[str, str] | None = None,
) -> Job: ...

async def get_by_id(store: JobStore, job_id: str) -> Job | None: ...

async def set_running(
    store: JobStore, job_id: str, *, prompt_id: str, client_id: str
) -> Job: ...

async def set_completed(
    store: JobStore, job_id: str, *, output_keys: list[str], result_json: str
) -> Job: ...

async def set_failed(
    store: JobStore, job_id: str, *, error_code: str, error_message: str
) -> Job: ...
```

Allowed transitions (anything else raises `InvalidTransition`):
```
queued    → running | failed | abandoned
running   → completed | failed | abandoned
completed → (terminal, no transitions)
failed    → (terminal)
abandoned → (terminal)
```

### 12.4 `app/logging_config.py`

```python
def configure_logging(level: str, log_prompts: bool) -> None:
    """Idempotent. Configure structlog + stdlib bridge."""

def redact_sensitive(logger, method_name, event_dict):
    """Structlog processor. Rules:
       - drop keys: presigned_url, Authorization, authorization, webhook.url
       - replace keys prompt/negative_prompt with "<redacted>" unless
         (log_prompts AND effective_level == DEBUG)
    """
```

Processor chain (order matters — redaction runs **after** exception formatting so a leaked prompt in a traceback dict is still scrubbed):
```
1. merge_contextvars
2. add_log_level
3. TimeStamper(fmt="iso", utc=True)
4. StackInfoRenderer
5. format_exc_info
6. redact_sensitive       ← last processor before the renderer
7. JSONRenderer
```

### 12.5 `app/middleware/logging.py`

```python
class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        """
        1. Read X-Request-Id (max 128 chars, alnum+hyphen), else uuid4().
        2. structlog.contextvars.bind_contextvars(request_id=...)
        3. Call next; measure wall time.
        4. Log one access line: method, path, status, duration_ms.
        5. clear_contextvars at end.
        6. Echo X-Request-Id back to client.
        """
```

### 12.6 `app/main.py` lifespan order

```
startup:
  1. configure_logging(level=LOG_LEVEL, log_prompts=LOG_PROMPTS)
  2. store = JobStore(DATABASE_PATH)
  3. await store.connect()           # opens + runs migrations
  4. app.state.store = store
  5. app.state.keyset = load_keyset_from_env()
  6. log event="service.started", version=__version__

shutdown:
  1. log event="service.stopping"
  2. await app.state.store.close()
```

### 12.7 Request lifecycle (Cycle 1 surface)

```
inbound HTTP
    │
    ▼
RequestContextMiddleware        bind request_id
    │
    ▼
FastAPI router                  path match
    │
    ▼
require_auth dependency         401/403 on fail; bind key_id on success
    │
    ▼
/health handler                 (only route this cycle)
    │
    ├─► store.healthcheck()     SELECT 1 with busy_timeout
    │
    ▼
response                        JSON body + X-Request-Id header
    │
    ▼
RequestContextMiddleware        log access line; clear_contextvars
```

### 12.8 Error envelope (shared by every non-2xx response in this cycle)

```json
{ "error": { "code": "<enum from arch §13>", "message": "<human-readable>" } }
```

Codes emitted in Cycle 1: `auth_error`, `not_found` (for unknown `/health` sub-paths? no — FastAPI default 404 body is fine here; `not_found` enum enters in Cycle 3 for unknown model id), `internal` (500 from unhandled exception handler — registered in `main.py`).

### 12.9 Migration runner contract

- Scans `migrations/` on startup, ordered by filename.
- Filename **must** match `^\d{3}_[a-z0-9_\-]+\.sql$` (e.g. `001_init.sql`, `002_webhook-deliveries.sql`); loader asserts strictly-ascending numeric prefix before running any file.
- For each unapplied file: `BEGIN; <file contents>; INSERT INTO schema_version(filename, applied_at) VALUES (?, ?); COMMIT;`
- If any statement fails, transaction rolls back and the whole startup fails (service exits non-zero).
- `schema_version` itself is created by the runner if missing (bootstrap path), before the scan.

---

*End of spec — design contracts closed.*
