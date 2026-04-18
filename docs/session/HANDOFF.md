# Handoff — next session start here

> This file is **overwritten** every session close. It reflects **current state**, not history.
> History lives in [SESSION.md](SESSION.md). Architecture lives in [docs/architecture/image-gen-service.md](../architecture/image-gen-service.md). Build plan lives in [docs/plans/2026-04-18-image-gen-service-build.md](../plans/2026-04-18-image-gen-service-build.md).

**Last updated:** 2026-04-18 — Session closed after Sprint 3 / Cycle 0.

---

## Where we are

- **Branch:** `main`, **3 commits ahead** of `origin/main` (not pushed).
- **Commits since origin:**
  - `1943d18` feat(cycle-0): repo bootstrap — FastAPI + Compose + tooling
  - `fced67d` docs: implementation plan (11 cycles) + Sprint 1 retro + Sprint 2 entry
  - `7d48d13` chore: install agentic workflow + draft image-gen-service architecture v0.4
- **Plan progress:** 1 / 11 cycles complete.

```
[x] 0  Repo bootstrap
[ ] 1  FastAPI + auth + SQLite + logging          ← NEXT
[ ] 2  ComfyUI sidecar + adapter + NoobAI workflow
[ ] 3  MinIO + first sync endpoint
[ ] 4  Queue + disconnect + reaper + restart
[ ] 5  LoRA local + injection
[ ] 6  Civitai fetcher hardened
[ ] 7  Chroma model #2
[ ] 8  Async + polling
[ ] 9  Webhook dispatcher
[ ] 10 Startup validation + smoke test
[ ] 11 LoreWeave integration-guide PR (parallel, user-owned)
```

- **Workflow state:** clean (last task `retro` completed). Reset ready for next cycle.

---

## Next action (Sprint 4 = Cycle 1)

**Goal per plan §Cycle 1:** Every request authenticated, every job persistable, every log line structured JSON with correlation id. Still no image generation.

Files to create / modify:
- `app/auth.py` — multi-key parser (`API_KEYS`, `ADMIN_API_KEYS`), `kid` derivation (first 8 chars SHA-256), `hmac.compare_digest`, FastAPI `Depends` helpers.
- `app/middleware/logging.py` — JSON logging + correlation id middleware.
- `app/queue/store.py` — `aiosqlite` wrapper + migration `migrations/001_init.sql` with the jobs table schema from arch §4.2 (incl. `response_delivered`, `initial_response_delivered`, `webhook_handover`, `webhook_url`, `webhook_headers_json`, `webhook_delivery_status`).
- `app/queue/jobs.py` — `Job` dataclass, CRUD helpers.
- `app/api/health.py` — `/health` returns 200 if DB reachable, 503 otherwise; auth-gated verbose shape.
- `app/main.py` — register middleware + lifespan handler that opens/closes SQLite.
- Tests: `tests/test_auth.py`, `tests/test_job_store.py`, update `tests/test_health.py` for DB probe.
- `config/logging.ini` or equivalent.
- `migrations/001_init.sql`.

Add deps (per pyproject.toml forward-compat comment):
- `aiosqlite>=0.20`
- `structlog>=24` (or stick with stdlib `logging` + JSON formatter — pick in Cycle 1 CLARIFY)

**Kickoff commands:**
```bash
cd d:/Works/source/local-image-generator-service
bash scripts/workflow-gate.sh reset
# then size classify honestly — plan says M (12 files), script may say L/XL
bash scripts/workflow-gate.sh size M 12 7 1   # files, logic, side_effects
bash scripts/workflow-gate.sh phase clarify
```

---

## Open items to resolve during Cycle 1 CLARIFY

- **structlog vs stdlib JSON logging** — structlog is nicer for async correlation-id context-vars but adds a dep. stdlib works too. Pick one before BUILD.
- **aiosqlite timeout + retry policy** — single writer, but background reaper + worker + HTTP handlers all write. Decide WAL mode (`PRAGMA journal_mode=WAL`) and `busy_timeout`.
- **Log prompt field** — arch §13 says prompts only at DEBUG with `LOG_PROMPTS=true` opt-in. Confirm default off.
- **SQLite location inside Docker** — plan says `./data/jobs.db`; confirm volume mount strategy (named volume vs bind mount). Named recommended for prod durability.

---

## Environment facts (persistent across sessions)

- **Host:** Windows 11, Docker Desktop, NVIDIA Container Toolkit working (verified Sprint 3).
- **GPU:** RTX 4090 visible in containers (CUDA 13.0, driver 581.80).
- **VRAM pressure:** 17.2 / 24 GB already in use on the host before any model load. Chroma Q8's ~9 GB will exceed the 12 GB budget → something must be freed before Cycle 7.
- **Port conflict:** `free-context-hub-minio-1` uses 9000/9001. Our dev Compose uses **127.0.0.1:9100/9101** for MinIO. Internal container port stays 9000.
- **Python:** `.python-version` pins 3.12 for Docker; host can be 3.13 (pyproject allows `<3.14`).
- **uv:** 0.9.11 installed on host. Dockerfile pins 0.9.11 too.
- **pyenv-win quirk:** `python3` shim mangles multi-line `-c` on Windows. `scripts/workflow-gate.sh` works around it by preferring `python`.

---

## Verify current state before starting next session

```bash
# confirm nothing regressed between sessions
cd d:/Works/source/local-image-generator-service
git status                                  # should be clean on main
bash scripts/workflow-gate.sh status        # should be empty / last retro complete
docker compose up -d                        # bring stack back up
curl -sf http://127.0.0.1:8700/health       # → {"status":"ok"}
uv run pytest -q                            # → 4 passed
uv run ruff check .                         # → All checks passed
```

If any of the above fails, read [SESSION.md](SESSION.md) Sprint 3 retro before diving in — the fix is probably there.

---

## External dependencies blocking no cycles yet

- **LoreWeave integration-guide PR (Cycle 11)** — user-owned, parallel. Soft-blocks Cycle 10 prod acceptance test. Not needed for Cycles 1–9 internally. Should draft before Cycle 9 integration tests.

---

## What NOT to do next session

- Do not start Cycle 2 (ComfyUI sidecar) before Cycle 1 lands — Cycle 1's SQLite + auth is a prereq for Cycle 3, and Cycle 2 without auth means the real ComfyUI adapter gets wired into an unauth'd endpoint first.
- Do not skip the workflow phases "because we've already designed it" — the design was v0.4 of arch doc; Cycle 1 CLARIFY still needs to resolve the open items above, however quickly.
- Do not commit a `docker-compose.override.yml` (dev-only, gitignored) — the `.example` is the canonical.
