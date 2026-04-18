# Session log

> Append the newest sprint at the top. Keep each entry short: one-line outcome, changed files, notable decisions, what's next.

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

