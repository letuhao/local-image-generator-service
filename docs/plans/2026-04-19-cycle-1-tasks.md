# Cycle 1 — task plan

> **Spec:** [docs/specs/2026-04-19-cycle-1-fastapi-auth-sqlite-logging.md](../specs/2026-04-19-cycle-1-fastapi-auth-sqlite-logging.md)
> **Size:** XL · **Execution mode:** Inline, sequential. No subagent dispatch — tasks have ordering dependencies (schema before store, store before health handler, etc.).
> **Checkpoints:** after every chunk, run the chunk's verification command. If red, fix before moving on.

---

## Chunk A — Scaffolding (no TDD; config-only tasks)

### A1. Dependencies + lockfile
**Files:** `pyproject.toml`
**Intent:** Add runtime deps `aiosqlite>=0.20,<0.21`, `structlog>=24.4,<25`, `svix-ksuid>=0.6.2,<0.7`. Delete `svix-ksuid` + `structlog` / `aiosqlite` mentions from the Cycle 1/6 forward-compat comment.
**Verify:** `uv sync` — exits 0 and updates `uv.lock`.

### A2. Env + gitignore + compose tweaks
**Files:** `.env.example`, `.gitignore`, `docker-compose.yml`
**Intent:**
- `.env.example`: append the 3 new vars under a `Logging (Cycle 1)` and `Storage: SQLite (Cycle 1)` section, matching spec §8.
- `.gitignore`: ensure `data/` and `data/*.db*` are covered.
- `docker-compose.yml`: on `image-gen-service`, add `volumes: - ./data:/app/data` and `DATABASE_PATH: /app/data/jobs.db` env.
**Verify:** `docker compose config` — exits 0, renders merged yaml without errors. Confirm new volume mapping present in the output.

### A3. Package markers
**Files:** `app/api/__init__.py`, `app/middleware/__init__.py`, `app/queue/__init__.py`
**Intent:** Three empty files (keep `__init__.py` zero-byte; ruff ignores empty files).
**Verify:** `ls app/api app/middleware app/queue` all succeed and show `__init__.py`.

### A4. Data dir + migration file
**Files:** `data/.gitkeep`, `migrations/001_init.sql`
**Intent:**
- Create `data/.gitkeep` so the bind-mount dir exists in the repo (empty file).
- Create `migrations/001_init.sql` with the schema from spec §7 verbatim (two tables, two indexes).
**Verify:** `python -c "import sqlite3; sqlite3.connect(':memory:').executescript(open('migrations/001_init.sql').read())"` — exits 0.

**Chunk A verification:** `uv sync && docker compose config >/dev/null && ls app/api/__init__.py app/middleware/__init__.py app/queue/__init__.py migrations/001_init.sql`.

---

## Chunk B — SQLite store + jobs CRUD (TDD)

### B1. Red: job-store tests
**Files:** `tests/test_job_store.py`
**Intent:** Write the 9 tests from spec §6 (round-trip, transitions, illegal-transition, concurrent creates, concurrent updates serialise, WAL pragma check, JobNotFound on unknown id). Use `pytest.fixture` for a fresh `JobStore` pointing at `tmp_path / "jobs.db"`.
**Verify:** `uv run pytest tests/test_job_store.py` — **fails** with ImportError on `app.queue.store` / `app.queue.jobs`.

### B2. Green: `app/queue/store.py`
**Files:** `app/queue/store.py`
**Intent:** `JobStore` class with `connect`, `close`, `write` (async context manager acquiring the lock), `read` (lock-free connection handle), `healthcheck`. Module-level `apply_migrations(conn, migrations_dir)` implementing the §12.9 contract. Pragmas as per §4.2.
**Verify:** tests for store-only primitives pass (WAL pragma, healthcheck). Job-CRUD tests still fail (no jobs module yet).

### B3. Green: `app/queue/jobs.py`
**Files:** `app/queue/jobs.py`
**Intent:** `Job` dataclass, `InvalidTransition` + `JobNotFound` exceptions, CRUD functions per §12.3. `create_queued` generates `gen_<ksuid>` using `ksuid.ksuid()`. All writes go through `store.write()` context manager.
**Verify:** `uv run pytest tests/test_job_store.py` — **all 9 pass**.

**Chunk B verification:** `uv run pytest tests/test_job_store.py -q` green.

---

## Chunk C — Auth (TDD)

### C1. Red: auth tests
**Files:** `tests/test_auth.py`
**Intent:** Tests from spec §6 — `parse_keys` (split/trim/dedup/empty), `kid_for` (8 hex chars), `require_auth` 401/403 matrix, `require_admin` 401/403/200 matrix, empty `API_KEYS` + Authorization header → 401.
**Setup:** Use a throwaway FastAPI app inside the test module (don't depend on `/health` yet — that's Chunk E).
**Verify:** `uv run pytest tests/test_auth.py` — **fails** with ImportError on `app.auth`.

### C2. Green: `app/auth.py`
**Files:** `app/auth.py`
**Intent:** `parse_keys`, `kid_for`, `AuthError`/`AuthScopeError`, `load_keyset_from_env()` returning a `_Keyset`, `require_auth`, `require_admin`. Both deps call `structlog.contextvars.bind_contextvars(key_id=kid)` on success.
**Verify:** `uv run pytest tests/test_auth.py -q` — all green.

**Chunk C verification:** auth tests green.

---

## Chunk D — Logging (TDD)

### D1. Red: logging tests
**Files:** `tests/test_logging.py`
**Intent:** Tests from spec §6 — JSON shape, `request_id` propagation (middleware-generated + header-sourced), `job_id` present inside CRUD log lines, `LOG_PROMPTS=false` redacts, `LOG_PROMPTS=true` + DEBUG renders, `LOG_PROMPTS=true` + INFO redacts, `presigned_url`/`Authorization` always dropped. Use `structlog.testing.capture_logs()` to capture events.
**Verify:** `uv run pytest tests/test_logging.py` — **fails** with ImportError on `app.logging_config`.

### D2. Green: `app/logging_config.py`
**Files:** `app/logging_config.py`
**Intent:** `configure_logging(level, log_prompts)` idempotent function. `redact_sensitive` processor. Processor chain per spec §4.1 with the reordering from §12.4 (redaction last).
**Verify:** the 4 logging tests that don't need the middleware pass; middleware-dependent tests still fail.

### D3. Green: `app/middleware/logging.py`
**Files:** `app/middleware/logging.py`
**Intent:** `RequestContextMiddleware(BaseHTTPMiddleware)` per spec §12.5. Read `X-Request-Id`, validate against `^[A-Za-z0-9\-]{1,128}$`, fallback to `uuid4().hex`. Bind contextvars, emit access line, clear contextvars at end, echo header on response.
**Verify:** `uv run pytest tests/test_logging.py -q` — all green.

**Chunk D verification:** logging tests green.

---

## Chunk E — Health + main wiring (TDD)

### E1. Red: updated health tests
**Files:** `tests/test_health.py`
**Intent:** Replace existing tests with the 6 tests from spec §6 (boolean vs verbose shape by auth, DB probe, 503 on unreachable DB, HEAD 200, POST 405).
**Verify:** `uv run pytest tests/test_health.py` — **fails** because `/health` doesn't yet check the DB / doesn't yet have an auth-gated verbose shape.

### E2. Green: `app/api/health.py`
**Files:** `app/api/health.py`
**Intent:** `APIRouter()` with `GET /health` + `HEAD /health`. Inline auth check (does not use `require_auth` as a dep — /health must accept unauthenticated requests and return boolean shape). If auth header absent or invalid → boolean shape. If auth header valid → call `store.healthcheck()` and render verbose shape. Return 503 if DB unreachable (both shapes).
**Verify:** health tests green up to the main-wiring dependency.

### E3. Green: update `app/main.py`
**Files:** `app/main.py`, `tests/conftest.py`
**Intent:**
- `app/main.py`: remove the inline `/health` handler; add `lifespan` that runs the §12.6 startup order; add `RequestContextMiddleware`; mount `health_router`; register global exception handler returning the §12.8 error envelope.
- `tests/conftest.py`: set required env (`API_KEYS`, `ADMIN_API_KEYS`, `DATABASE_PATH=tmp_path/jobs.db`, `LOG_LEVEL=INFO`, `LOG_PROMPTS=false`) via a `monkeypatch` session-scoped fixture **before** `from app.main import app` happens. Add a `store` fixture yielding the app's store (via `app.state.store`) for direct use in `test_job_store.py`.
**Verify:** `uv run pytest tests/test_health.py -q` — all green.

**Chunk E verification:** health tests green.

---

## Chunk F — Full-suite + smoke

### F1. Full unit run
**Verify:** `uv run pytest -q` — all prior-cycle tests still green + all new tests green. Expect ≥ 4 (Cycle 0) + test_auth (~7) + test_job_store (~9) + test_logging (~6) + test_health (updated, ~6). Target: ≥ 30 pass, 0 fail.

### F2. Lint + type
**Verify:** `uv run ruff check .` — All checks passed. `uv run ruff format --check .` — No changes.

### F3. Docker build + smoke
**Verify:**
```
docker compose build image-gen-service
docker compose up -d
# Wait for healthy
curl -sf http://127.0.0.1:8700/health
# Expect: {"status":"ok"}
# With valid generation key (generate one first; export API_KEY in host env after editing .env):
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/health
# Expect: {"status":"ok","db":"ok"}
```

### F4. Plan-verification command (from main plan Cycle 1)
```
pytest -q tests/test_auth.py tests/test_job_store.py tests/test_health.py && \
docker compose up -d && \
curl -sf -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8700/health | jq .
```

---

## Order of execution (strict)

```
A1 → A2 → A3 → A4        # scaffolding
 ↓
B1 → B2 → B3             # store + jobs (TDD, can RED stay red across B1→B3)
 ↓
C1 → C2                  # auth
 ↓
D1 → D2 → D3             # logging
 ↓
E1 → E2 → E3             # health + wiring
 ↓
F1 → F2 → F3 → F4        # verify
```

## Commit checkpoints

Single commit at end of Cycle 1 — no per-chunk commits. The cycle ships one coherent slice; partial commits would leave the test suite red between chunks.

Commit message template:
```
feat(cycle-1): FastAPI auth + SQLite job store + structured JSON logging

- app/auth.py: multi-key Bearer auth with kid logging, hmac.compare_digest
- app/queue/{store,jobs}.py: aiosqlite WAL + migration runner + Job CRUD
- app/logging_config.py + app/middleware/logging.py: structlog JSON + request_id
- app/api/health.py: DB probe + auth-gated verbose shape
- migrations/001_init.sql: full jobs schema from arch §4.2
- tests: auth, job_store, logging, updated health — all green
```

## Risks during BUILD

| Risk | Mitigation during build |
|---|---|
| ASGI middleware ordering (auth must see `request_id`) | Add `RequestContextMiddleware` LAST so it runs OUTERMOST — request goes through it first, auth runs after. |
| `app.state.store` not available in tests before lifespan runs | `conftest.py`'s `client` fixture already wraps in `LifespanManager`; confirm `store` fixture depends on `client` (not a separate instantiation). |
| Test pollution from structlog global state | Fixture resets structlog via `structlog.reset_defaults()` + re-call `configure_logging`. |
| `uv sync` pulls ksuid but Docker layer cache still has old lock | `docker compose build --no-cache` if F3 smoke shows import errors. |
| SQLite file locking on Windows during test teardown | Ensure store fixture `await store.close()` in teardown; use `tmp_path` so Windows doesn't reuse the path across tests. |

---

*End of task plan.*
