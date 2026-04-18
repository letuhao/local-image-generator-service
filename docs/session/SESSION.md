# Session log

> Append the newest sprint at the top. Keep each entry short: one-line outcome, changed files, notable decisions, what's next.

**Last session ended:** 2026-04-18 after Sprint 3 / Cycle 0 complete. Resume from [HANDOFF.md](HANDOFF.md) — it holds the pick-up-where-you-left-off summary.

---

## Sprint 3 — 2026-04-18 — Cycle 0 repo bootstrap complete

**Outcome:** `docker compose up` brings up three services on a private network (`image-gen-service`, `comfyui` placeholder, `minio`). `curl http://127.0.0.1:8700/health` returns `{"status":"ok"}`. Unit tests green (4/4), ruff clean, image size 285 MB. All three containers Docker-healthy. Ready for Cycle 1 (auth + SQLite + structured logging).

**GPU toolkit check (per plan prerequisite):** passed. RTX 4090 visible, driver 581.80, CUDA 13.0 inside containers. **Flag for Cycle 7:** 17.2 / 24 GB VRAM in use on host before load — only ~7 GB free. Chroma Q8's 9 GB floor will exceed budget unless something is freed before Cycle 7.

**Files created (14):**

- `pyproject.toml`, `.python-version`, `.dockerignore`, `.env.example`, `.pre-commit-config.yaml`
- `Dockerfile` (two-stage: builder with uv → runtime without), `docker/entrypoint.sh`
- `docker-compose.yml`, `docker-compose.override.yml.example`, `docker/comfyui-placeholder/default.conf`
- `app/__init__.py`, `app/main.py`
- `tests/__init__.py`, `tests/conftest.py`, `tests/test_health.py`
- `README.md` rewritten from a one-liner to a real README

Also: `.gitignore` extended with runtime-data paths (`data/`, `minio-data/`, `models/`, `loras/`, `docker-compose.override.yml`).

**`/review-impl` pass found 14 findings, all fixed in v0.1.1:**

- HIGH: MinIO healthcheck curl-only → curl-with-wget-fallback; Python pin excluded 3.13 host → relaxed to `<3.14` + `.python-version` pins image to 3.12.
- MED: two-stage Dockerfile drops ~30 MB of uv dead weight (315 → 285 MB); `entrypoint.sh` propagates `SHUTDOWN_GRACE_S` to `uvicorn --timeout-graceful-shutdown`; `depends_on: comfyui: service_healthy`; ruff `S104` ignore + policy comment; `.dockerignore` symmetric on override files; HEAD + Content-Type tests (also discovered `@app.get` doesn't auto-register HEAD, fixed with `api_route`).
- LOW: Dockerfile `HEALTHCHECK` directive using `urllib.request` (no extra binary); `asgi-lifespan.LifespanManager` in `conftest.py` so future startup hooks fire in tests; `.env.example` `API_KEYS=` blank for fail-closed posture; forward-compat deps documented per-cycle.

**Live verification:**

```
pytest    4/4 pass
ruff      clean
curl /health    {"status":"ok"}
compose ps      all three services Up (healthy)
PID 1 in container    uvicorn ... --timeout-graceful-shutdown 90
```

**Dev-env note:** moved MinIO dev ports to 127.0.0.1:9100/9101 because `free-context-hub-minio-1` already occupies 9000/9001 on this host. Base compose still uses internal port 9000.

### Retro — lessons worth keeping

- **`/review-impl` on a trivial cycle still catches material issues** — 14 findings on what I'd called "boilerplate". Two were genuine HIGHs (MinIO healthcheck, Python pin). Author blindness is real even on config files. Budget ~5–10 min per cycle for the adversarial pass.
- **`@app.get` in FastAPI does NOT auto-register HEAD.** Reviewer flagged HEAD coverage gap; writing the test revealed FastAPI's actual behavior. Use `api_route(methods=["GET","HEAD"])` for probe endpoints.
- **Python pin ceilings are load-bearing.** `>=3.12,<3.13` appeared conservative but actually excluded the dev host (3.13). Relax ceilings to match reality, pin the *floor* via `.python-version` for Docker determinism.
- **First `uv sync` in Docker will try to build the project unless `--no-install-project`.** Hatchling demands README.md at build time even if it's not copied yet. The `--no-install-project + PATH=.venv/bin` pattern is the clean way to avoid that.

**What's next (Sprint 4 plan):**

1. Start Cycle 1 — `app/auth.py` (multi-key parser, constant-time compare), `app/queue/store.py` (aiosqlite layer + migration 001), `app/middleware/logging.py` (JSON logs + correlation id), `app/api/health.py` (deep probe of DB + dependents).
2. Tests: `test_auth.py`, `test_job_store.py`, updated `test_health.py` for dependent-down → 503.
3. Still no image generation in Cycle 1 — that's Cycle 2.

**Commits this sprint:** 1 expected (all Cycle 0 + Cycle 0 v0.1.1 fixes).

---

## Sprint 2 — 2026-04-18 — Implementation plan written (11 cycles)

**Outcome:** Decomposed the v0.4 architecture into 11 vertical-slice cycles with explicit dependencies, per-cycle files/tests/verification/descope, and an owner-assigned unknowns table. Each cycle is independently runnable through the 12-phase workflow. Ready to CLARIFY Cycle 0.

**New files:**

- `docs/plans/2026-04-18-image-gen-service-build.md` — 569 lines, 11 cycles + LoreWeave integration PR + dependency graph + per-cycle prereqs table.
- `docs/session/SESSION.md` — Sprint 1 retro lessons appended (post-Sprint-1 commit, landing now).

**Cycle shape decisions:**

- Vertical slices only — each cycle ships a thing that works end-to-end, never a horizontal layer.
- Every cycle has an explicit descope list — scope-creep catcher.
- Cycle 9 (webhook dispatcher) is sized XL alone — the v0.4 hardening is dense enough to warrant its own sprint, subagent dispatch recommended.
- Cycle 11 (LoreWeave PR) is parallel, soft-blocks only Cycle 10's prod-mode acceptance test.

**What's next (Sprint 3 plan):**

1. Verify Docker Desktop + NVIDIA Container Toolkit on the Win11 host (listed as Cycle 0 prerequisite in the plan).
2. Begin Cycle 0 (repo bootstrap) — reset workflow, classify size S, run phases through to commit.
3. In parallel, start drafting the LoreWeave integration-guide amendment (Cycle 11) so it lands before Cycle 9 tests.

**Commits this sprint:** 1 expected (plan doc + Sprint 1 retro tail that didn't make the Sprint 1 commit).

---

## Sprint 1 — 2026-04-18 — Agentic workflow installed + architecture v0.4 drafted

**Outcome:** Project bootstrapped with a 12-phase agentic workflow enforcement layer; architecture spec for the image-generation service written and reviewed through four adversarial passes (initial draft, 4-perspective review, `/review-impl` on webhook surfaces, fix-all response). Ready for BUILD to begin.

**New / modified files:**

- `scripts/workflow-gate.sh` — installed; patched to prefer `python` over pyenv-win's broken `python3` shim that mangles multi-line `-c`.
- `.claude/settings.json` — pre-commit hook blocks commits without VERIFY + POST-REVIEW + SESSION.
- `.claude/commands/review-impl.md` — on-demand adversarial review command.
- `CLAUDE.md` — full workflow pasted in.
- `.gitignore` — `.workflow-state.json` added.
- `docs/architecture/image-gen-service.md` — draft v0.1 → v0.2 → v0.3 → **v0.4**, 1276 lines, 20 sections.
- `docs/session/SESSION.md` — this file.

**Review rounds and what changed:**

1. **v0.1 → v0.2 (four-perspective review).** Four parallel agents (architect, security, QA, dev) surfaced 7 convergent HIGHs + 3 unique HIGHs + ~10 MEDs + ~5 LOWs. Biggest fixes: SQLite job store (not in-memory), ComfyUI anchor-node convention, 11-rule Civitai fetch hardening, prod network posture with startup assertion, full ComfyUI prompt-API contract (`client_id`, WebSocket, `/interrupt` + `/free` + `DELETE /queue`).
2. **v0.2 → v0.3 (webhook addition).** User requested webhook notifications. Added §4.8 dispatcher (separate asyncio task, HMAC signing, 5-attempt retry, persistent queue), `webhook_deliveries` SQLite table, `/v1/webhooks/deliveries/{id}` admin endpoint, integration-guide expansion.
3. **v0.3 → v0.4 (`/review-impl` on webhook surfaces).** Dedicated adversarial reviewer found 14 findings on webhook signing, SSRF, and sync/dispatcher state-machine. All resolved: DNS pinning, IP-range filter, no-redirect policy, Stripe-style timestamp+body signing (`t=<ts>,v1=<hex>` header, 300 s skew), multi-secret rotation (`WEBHOOK_SIGNING_SECRETS` plural set), TOCTOU re-validation per attempt, `webhook_handover` SQLite barrier, fail-closed allowlist (deny-all by default), `IMAGEGEN_ENV={prod,dev}` explicit mode switch, literal Go receiver reference implementation.

**Decisions locked:**

- Runtime: ComfyUI sidecar (separate container), not embedded.
- Day-1 models: NoobAI-XL Vpred-1.0 (SDXL, ~7 GB VRAM) + Chroma1-HD Q8 GGUF (~9 GB VRAM); stay under 12 GB budget (50 % of RTX 4090).
- Storage: MinIO locally → real S3 on Novita, swap via env.
- Queue: in-process asyncio, **one GPU worker** for v1, scale later.
- Async mode gated off (`ASYNC_MODE_ENABLED=false`) until integration-guide amendment lands.
- LoRAs: local directory scan + Civitai fetch with 11-rule hardening.
- Auth: multi-key set with `kid` logging; admin scope separate from generation scope.
- Webhook delivery: terminal events only, at-least-once, HMAC-SHA256 with timestamp binding, durable receiver dedupe required.

**Out of scope (deferred, §17 in arch doc):** HF repo paths for pre-download script, SSE progress streaming, img2img / inpaint / ControlNet (v2), per-caller quota, Prometheus/OTel, multi-hash Civitai verify, sidecar trust re-verify, ComfyUI zero-downtime custom-node swap.

**Owner dependencies (external to this repo):**

- @letuhao1994 to land the LoreWeave integration-guide amendment PR covering: async mode, webhook field, Go receiver reference implementation, durable dedupe mandate, timestamp freshness rule, suggested `POST /v1/webhooks/image-gen` route.

**What's next (Sprint 2 plan):**

1. Pick the BUILD order — probably vertical slice: FastAPI skeleton + auth + SQLite job store + sync endpoint + ComfyUI adapter (HTTP + WS) + NoobAI workflow template + MinIO upload. No webhook, no LoRA injection, no async, no Civitai fetch in sprint 2.
2. Write the custom `docker/comfyui/Dockerfile` with pinned ComfyUI + `ComfyUI-GGUF` + T5 encoder.
3. Write `workflows/sdxl_vpred.json` with anchor-tagged nodes (`%MODEL_SOURCE%`, `%CLIP_SOURCE%`, `%KSAMPLER%`, `%OUTPUT%`).
4. Confirm Docker Desktop + NVIDIA Container Toolkit is working on Win11 host before BUILD.
5. Verify LoreWeave's HTTP client timeout for sync path (operational risk #1 from pre-BUILD concerns).

**Commits this sprint:** 1 expected (this sprint's doc + workflow scaffold).

### Retro — lessons worth keeping

- **`/review-impl` caught 14 real webhook issues that the earlier 4-agent parallel review missed on the same surfaces.** The parallel review was broad; `/review-impl` went narrow + adversarial. Both are needed — they don't substitute. Author blindness persisted across the four roles because all four implicitly trusted that "a webhook is a webhook, signing is signing"; the dedicated adversarial pass asked "what specifically is `sha256=<hex>` vs `t=<ts>,v1=<hex>`, what DNS pins, what redirects?" and found real gaps. **Lesson:** on safety-sensitive surfaces, budget for both a broad review AND a focused adversarial pass.
- **`python3` on pyenv-win mangles multi-line `-c` arguments** (injects what looks like Windows batch noise). Fix: `scripts/workflow-gate.sh` now prefers `python` first, tests multi-line capability, falls back. If we ever hit this pattern in the service code too (we probably won't — the service uses stdlib subprocess, not ad-hoc `python -c`), the lesson is: detect capability, don't trust the name.
- **v0.1 doc was written without counting anchors.** The ComfyUI adapter section said "find LoraLoader by convention" without picking the convention. Ended up introducing `_meta.title = "%ANCHOR%"` in v0.2 as a formal pattern. **Lesson:** if you catch yourself writing "by convention" without naming the convention, you haven't finished the design.

