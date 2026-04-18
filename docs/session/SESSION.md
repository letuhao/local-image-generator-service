# Session log

> Append the newest sprint at the top. Keep each entry short: one-line outcome, changed files, notable decisions, what's next.

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
