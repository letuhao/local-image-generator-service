# local-image-generator-service — Architecture

> **Status:** Draft v0.4 — 2026-04-18
> **Owner:** @letuhao1994
> **Related:** [EXTERNAL_AI_SERVICE_INTEGRATION_GUIDE.md](../EXTERNAL_AI_SERVICE_INTEGRATION_GUIDE.md)
> **Changes since v0.3:** webhook hardening — DNS pinning, SSRF IP-range check, redirect policy, anti-replay signing, multi-secret rotation, TOCTOU re-validation, sync/dispatcher barrier, fail-closed allowlist, explicit env mode. See §20 Change log.

---

## 1. Purpose

Build a self-hostable, multi-model, multi-runtime image generation service that:

- Exposes an **OpenAI-compatible HTTP API** so it plugs into LoreWeave's `provider-registry-service` with no custom adapter.
- Acts as a **dispatcher** over one or more local/remote image-gen backends (ComfyUI first).
- Supports **uncensored community models** (NoobAI-XL, Chroma1-HD, Illustrious merges) without per-model server code.
- Lets users **toggle models at request time** via the `model` field.
- Runs in **Docker Compose locally** and on **Novita GPU** cloud with only env-var changes.

Non-goals (v1):

- Training or fine-tuning models.
- Building our own LoRAs.
- Content moderation / safety classifiers (the service is intentionally permissive; any policy sits upstream in LoreWeave).
- Video generation (separate service in LoreWeave).
- Stable public hosting / multi-tenancy beyond a small allow-listed set of API keys.

---

## 2. Context

LoreWeave already defines an integration contract for external image services — see [section 6 of the integration guide](../EXTERNAL_AI_SERVICE_INTEGRATION_GUIDE.md). This service implements that contract and extends it where noted in §10 of this doc.

Key contract summary:

- `POST /v1/images/generations` — returns `{ data: [{ url }] }`; LoreWeave downloads the URL and stores in its own MinIO.
- `GET /v1/models` — discovery endpoint; also used as a health probe.
- `GET /health` — optional, returns 200.
- `Authorization: Bearer <key>` — a static API key (one of a small active set; see §11) shared with LoreWeave.

> **Blocking dependency:** async mode (§6.2) requires an addendum to the integration guide so LoreWeave's adapter accepts a `202`+poll pattern on `/v1/images/generations`. Until that PR lands, async is opt-in and off by default (`ASYNC_MODE_ENABLED=false`). Sync mode is the sole externally-guaranteed path for v1.

---

## 3. High-level architecture

```
                    ┌───────────────────────────────────┐
                    │    LoreWeave provider-registry    │◀─ webhook POST ─┐
                    └──────────────┬────────────────────┘                 │
                                   │  OpenAI-compatible HTTP              │
                                   ▼                                      │
  ┌───────────────────────────────────────────────────────────────┐       │
  │  image-gen-service (this repo)                                │       │
  │                                                               │       │
  │  ┌────────────┐   ┌──────────────┐   ┌──────────────────────┐ │       │
  │  │ FastAPI    │──▶│  Job Queue   │──▶│  Backend Router      │ │       │
  │  │ endpoints  │   │ (asyncio,    │   │                      │ │       │
  │  │ + auth +   │   │  1 worker)   │   │  ├─ ComfyUI adapter ─┼─┼──▶ ComfyUI
  │  │ validation │   │  + SQLite    │   │  ├─ Diffusers (stub) │ │       │
  │  └─────┬──────┘   │  job store   │   │  └─ Remote-API (stub)│ │       │
  │        │          └──────┬───────┘   └──────────────────────┘ │       │
  │        │                 ▼                                    │       │
  │        │        ┌─────────────────┐                           │       │
  │        │        │ Model Registry  │  (YAML + startup check)   │       │
  │        │        └─────────────────┘                           │       │
  │        ▼                                                      │       │
  │  ┌───────────────────┐   ┌──────────────────┐                 │       │
  │  │  Output uploader  │──▶│  MinIO (S3 API)  │                 │       │
  │  │  (retry + presign)│   └──────────────────┘                 │       │
  │  └───────────────────┘                                        │       │
  │                                                               │       │
  │  ┌───────────────────────────────────────────────────────┐    │       │
  │  │  Webhook dispatcher (separate asyncio task)           │────┼───────┘
  │  │  persisted queue (SQLite) · HMAC-SHA256 signing       │    │
  │  │  5-attempt retry: 15s → 1m → 5m → 15m → 1h            │    │
  │  └───────────────────────────────────────────────────────┘    │
  └───────────────────────────────────────────────────────────────┘
                                   ▲
                                   │ (private Compose network only)
  ┌────────────────────────────────┴───────────────────────────────┐
  │  ComfyUI sidecar container (custom Dockerfile, pinned digest)  │
  │  - Custom nodes: ComfyUI-GGUF (pinned commit), KJNodes         │
  │  - Volumes:  /models (ro*), /loras (ro*), /workflows (ro)      │
  │  - HTTP: /prompt /history /view /interrupt /queue  WS: /ws     │
  │  (* ro for ComfyUI; only the service has write access)         │
  └────────────────────────────────────────────────────────────────┘
```

---

## 4. Components

### 4.1 FastAPI gateway

Single Python process, async. Responsibilities:

- Multi-key Bearer auth with key-id logging (middleware).
- Input validation via Pydantic v2 (every numeric and size field bounded — see §6.1).
- Routing: sync vs async mode.
- Job enqueue / status lookup.
- Output URL signing + response shaping.

### 4.2 Job queue & store

Queue: `asyncio.Queue`, one worker task. Store: **SQLite** (`./data/jobs.db`) — not in-memory. Job rows persist across restarts.

Job schema:

```
id                        TEXT PRIMARY KEY  (format "gen_<ksuid>")
model_name                TEXT
input_json                TEXT
mode                      TEXT              -- "sync" | "async"
status                    TEXT              -- queued | running | completed | failed | abandoned
result_json               TEXT NULL
error_code                TEXT NULL         -- enum, see §13
error_message             TEXT NULL
created_at                TEXT (ISO-8601)
updated_at                TEXT (ISO-8601)
client_id                 TEXT              -- ComfyUI prompt client_id (uuid4)
prompt_id                 TEXT NULL         -- ComfyUI prompt_id, after submission
output_keys               TEXT NULL         -- JSON array of S3 object keys
response_delivered        BOOL DEFAULT 0    -- true after sync response written to client
initial_response_delivered BOOL DEFAULT 0   -- true after async 202 written to client
webhook_url               TEXT NULL         -- from request; present if caller wants push notify
webhook_headers_json      TEXT NULL         -- caller-supplied passthrough headers
webhook_delivery_status   TEXT NULL         -- pending | succeeded | failed | suppressed
webhook_handover          BOOL DEFAULT 0    -- sync handler has handed off to dispatcher
```

**Delivery semantics are at-least-once, end to end.** Two crash-consistency scenarios produce duplicate observable events; receivers MUST tolerate them:

- *Sync flush race:* response bytes go on the wire, process crashes before `response_delivered=true` commits to SQLite. On boot, dispatcher sees "status=completed, response_delivered=false", fires the webhook. Client already received the sync result AND now gets a webhook for the same `job_id`.
- *Dispatcher retry after receiver commits but ack is lost:* LoreWeave persisted the result, its 200 response was lost in transit, we retry. Same `job_id`, same payload.

**Required receiver behavior** (encoded in §10):
- Dedupe by `X-ImageGen-Job-Id` using **durable storage** (not in-memory). A receiver that dedupes only in RAM will double-process after its own restart.
- 2xx as soon as the delivery is persisted — do not wait for downstream pipelines to finish.

**Sync disconnect handling.** Sync requests await the job's completion future inside an `asyncio.shield` block; a `Request.is_disconnected()` listener flips the job's `mode` to `async` if the client drops, so the worker still finishes, writes the result, and the record is recoverable by job id (returned in a `X-Job-Id` response header on sync acceptance).

**Restart recovery.** On boot, the service scans SQLite for jobs in `queued`/`running`:
- `queued` → re-enqueue (idempotent).
- `running` → mark `failed` with `error_code="service_restarted"` and emit the terminal status so async pollers get an answer rather than a 404.

**Seed non-determinism on recovery.** When a caller submits `seed=-1` (the OpenAI "random" sentinel), the resolved random seed is chosen by the worker at pipeline time and persisted in `result_json.resolved_seed`. If the job is `queued` at restart time (never reached the worker), recovery re-runs the pipeline and picks a **new** random seed — the same caller who saw `X-Job-Id=gen_xxx` and polls later will see a different image than the first attempt would have produced. Callers who care about reproducibility across restarts MUST pass an explicit seed; `-1` implies "I accept whatever the service picks, even across retries."

**Orphan reaper.** A background task deletes S3 objects belonging to jobs that reached `completed` but whose result was never fetched within `ORPHAN_REAPER_TTL` (default 24h). MinIO also has a bucket lifecycle policy (belt and braces).

**TTL eviction.** SQLite rows are pruned at `JOB_RECORD_TTL` (default 7d).

### 4.3 Backend router & ComfyUI adapter

Backend router dispatches by `model_name → backend` via the Model Registry (§4.4).

Adapter Protocol:

```python
class BackendAdapter(Protocol):
    async def generate(self, job: Job, model_cfg: ModelConfig) -> GenerationResult: ...
    async def health(self) -> HealthStatus: ...
    async def list_models(self) -> list[str]: ...
    async def cancel(self, prompt_id: str) -> None: ...
```

#### ComfyUI adapter — full HTTP/WS contract

Real ComfyUI API is polling-capable but canonical completion comes over WebSocket. The adapter uses both:

1. **Prepare workflow.** Load the template JSON (ComfyUI "prompt-API" format — a dict of node id → `{class_type, inputs: {...}}`), resolve anchor nodes (see §9), inject LoRAs and prompt params.
2. **Submit.** `POST http://comfyui:8188/prompt` with body `{"prompt": <graph>, "client_id": "<uuid4-per-adapter-instance>"}`. Response: `{"prompt_id": <uuid>, "number": <queue_pos>, "node_errors": {...}}`. Store `prompt_id` in the job row.
3. **Watch for completion.** Connect to `ws://comfyui:8188/ws?clientId=<client_id>` and wait for `{"type":"executing","data":{"node":null,"prompt_id":"<pid>"}}` (canonical "done"). Fall back to polling `/history/{prompt_id}` every `COMFY_POLL_INTERVAL_MS` (default 1000 ms) if the WS disconnects, capped by `JOB_TIMEOUT_S` (default 300 s).
4. **Collect outputs.** On completion, read `history[prompt_id].outputs`; iterate nodes, find the one(s) with `images: [...]` (identified by anchor — see §9 — rather than by hardcoded node id). For each image entry:

   ```
   GET /view?filename=<fn>&subfolder=<sf>&type=<output|temp>
   ```

   streams the PNG bytes.
5. **Upload + presign** — hand off to §4.6.

**Cancellation.** ComfyUI has no per-request cancel — `POST /interrupt` aborts whatever is currently executing *globally*, and `DELETE /queue` with `{"delete": [prompt_id]}` removes a queued-but-not-started prompt. The adapter uses both based on job state, and explicitly **does not** promise immediate VRAM release; after interrupt, the adapter calls `POST /free {unload_models: true, free_memory: true}` (available on recent ComfyUI) and verifies `/system_stats` before accepting the next job.

**Crash detection.** Worker poll loop enforces a cumulative deadline; connection-refused on `/history` or `/system_stats` → job `failed{error_code="comfy_unreachable"}`, adapter health flips to `down`.

### 4.4 Model Registry

Static YAML at `config/models.yaml`, loaded at startup and reloadable via `POST /admin/reload` (auth-gated; no SIGHUP — Windows dev target). Reload swaps the registry atomically behind an `asyncio.Lock`; in-flight jobs keep the snapshot they started with.

**Startup validation (fail-fast):**
- Each entry's `checkpoint` file exists at the expected path under `./models/`.
- Each entry's `workflow` file exists and is valid JSON with the required anchor nodes (§9).
- Each entry's `vram_estimate_gb` ≤ `VRAM_BUDGET_GB` env (default 12 for 50% of 4090).
- If any fails, the service refuses to start with a clear message pointing at `scripts/pull-models.sh`.

Example:

```yaml
models:
  - name: noobai-xl-v1.1
    backend: comfyui
    workflow: workflows/sdxl_eps.json
    checkpoint: checkpoints/NoobAI-XL-v1.1.safetensors
    prediction: eps             # "vpred" | "eps" — Cycle 2 uses eps, vpred injection deferred (§9)
    vae: vae/sdxl_vae.safetensors
    capabilities: { image_gen: true }
    defaults:
      size: "1024x1024"
      steps: 28
      cfg: 5.0
      sampler: euler_ancestral  # KSampler.sampler_name
      scheduler: karras         # KSampler.scheduler
      negative_prompt: "worst quality, low quality"
    limits:
      steps_max: 60
      n_max: 4
      size_max_pixels: 1572864  # 1024x1536 ceiling
    vram_estimate_gb: 7

  - name: chroma-hd-q8
    backend: comfyui
    workflow: workflows/chroma_gguf.json
    checkpoint: chroma1-hd-q8.gguf
    vae: ae.safetensors
    clip_l: clip_l.safetensors
    t5xxl: t5xxl_fp8_e4m3fn.safetensors
    dual_clip_type: chroma
    capabilities: { image_gen: true }
    defaults:
      size: "1024x1024"
      steps: 30
      cfg: 4.5
      sampler: euler
      scheduler: simple
    limits:
      steps_max: 50
      n_max: 2
      size_max_pixels: 1572864
    vram_estimate_gb: 9
```

`GET /v1/models` reads from this registry; `limits` are enforced as hard caps at request validation (§6.1) and the request's `defaults` fill missing fields.

### 4.5 LoRA manager

Two sources, single directory `./loras/` mounted **writable by the service** and **read-only by ComfyUI** (see §5).

**Directory scan.** `GET /v1/loras` walks `./loras/` and returns `{name, filename, source, civitai_model_id?, civitai_version_id?, sha256, base_model_hint, trigger_words[]}`. Sidecar metadata lives at `<name>.json`.

**Civitai fetch.** `POST /v1/loras/fetch`:

```json
{
  "civitai_model_id": 12345,
  "civitai_version_id": 67890,          // REQUIRED — no "latest" ambiguity
  "expected_sha256": "<optional override>"
}
```

Or via URL (the service parses `civitai.com/models/<id>(?modelVersionId=<vid>)?`):

```json
{
  "civitai_url": "https://civitai.com/models/12345?modelVersionId=67890"
}
```

**Hardening (all MUST hold):**

1. Host allowlist: only `civitai.com` and its CDN redirect hosts (resolved from the metadata endpoint's response); reject anything else.
2. `version_id` is **required** — no implicit "latest".
3. Fetch is a two-step: first `GET https://civitai.com/api/v1/models/<model_id>` with `Authorization: Bearer ${CIVITAI_API_TOKEN}` (NSFW-gated assets require auth), then pick `files[]` where `primary == true` and `downloadUrl` is used with `follow_redirects=True`.
4. SHA-256 verification: downloaded bytes are hashed and compared against `files[].hashes.SHA256` from the metadata response (or the explicit `expected_sha256`). Mismatch → quarantine directory, 422 response.
5. Extension allowlist: `.safetensors` only.
6. File-size cap: `LORA_MAX_SIZE_BYTES` (default 2 GiB).
7. Total `./loras/` volume ceiling with LRU eviction when `LORA_DIR_MAX_SIZE_GB` is exceeded (default 60 GiB).
8. Per-URL `asyncio.Lock` keyed on `(model_id, version_id)` — concurrent requests for the same LoRA wait for the first and share the result.
9. Admin-scoped token required: requests must present a key in the `admin` scope (§11), not the generation scope.
10. Concurrency: `LORA_MAX_CONCURRENT_FETCHES=1` (serial).
11. Audit log line on every fetch attempt (see §13).

**Apply.** Request payload carries `loras: [{name, weight}]`. The ComfyUI adapter injects `LoraLoader` nodes between the model/clip anchors and downstream consumers (algorithm in §9). LoRA names are validated against `^[A-Za-z0-9_][A-Za-z0-9_\-.]*$` (no path components); missing files return `error_code="lora_missing"` before workflow submission.

### 4.6 Output uploader (MinIO / S3) — v0.6 backend gateway model

Flow: after ComfyUI returns PNG bytes → upload to S3 (internal only) → respond with a URL pointing at **our** service → caller GETs the image through us.

**Single-client model:**
- `S3_INTERNAL_ENDPOINT` (default `http://minio:9000`) — the only S3 endpoint configured. One boto3 client.
- No presigned URLs in Cycle 3+ (v0.6 amendment). Callers never touch S3 directly.

**Response URL shape.** `data[].url = <IMAGE_GEN_PUBLIC_BASE_URL>/v1/images/<job_id>/<index>.png`, e.g. `http://127.0.0.1:8700/v1/images/gen_abc123/0.png`. Base URL is operator-configured, normalized at load time (trailing slash stripped, scheme required).

**Image fetch gateway** — `GET /v1/images/{job_id}/{index}.png` (see §6.X). Authenticates via the same Bearer key as the POST; streams bytes from S3 back to the caller. No time-boxed URLs; caller must hold a valid key for the duration.

**Upload retry.** 3 attempts with jittered exponential backoff (`500ms`, `1.5s`, `4.5s`). On terminal failure → `error_code="storage_error"`.

**Object key layout:** `generations/<YYYY>/<MM>/<DD>/<job_id>/<index>.png` (unchanged from v0.5).

**Log redaction.** Object keys are fine to log as `(bucket, key)` tuples. The gateway URL is safe to log (it's auth-gated by Bearer, not by URL-embedded tokens). Error response bodies return the opaque gateway URL, never the internal object key.

**Rationale for the gateway redirect (v0.6):** unified auth (no separate presign credential surface), exact fetch observability (orphan reaper sees every fetch directly in Cycle 4), simpler code. Trade-off is bandwidth amplification through the uvicorn process — acceptable at LoreWeave's scale; revisitable post-v1.

### 4.7 ComfyUI sidecar — custom nodes & Dockerfile

ComfyUI stock doesn't load GGUF or Chroma's T5 variant. The sidecar image is a **custom Dockerfile** (not an arbitrary off-the-shelf image) pinning:

| Component | Pin | Why |
|---|---|---|
| ComfyUI | git tag + commit | Reproducible; immune to breaking node-id changes |
| `city96/ComfyUI-GGUF` | commit | `UnetLoaderGGUF`, `DualCLIPLoaderGGUF` for Chroma |
| `kijai/ComfyUI-KJNodes` (optional) | commit | Useful utility nodes; only if workflows use them |
| T5 XXL FP8 encoder | downloaded at build or on first run | Required by Chroma workflow |

The full node pin list lives in `docker/comfyui/custom-nodes.txt`. `docker/comfyui/Dockerfile` `RUN git clone --depth 1 --branch <tag> ... && git checkout <commit>` for each.

Adapter performs a **startup smoke test** that runs a tiny workflow against each registered model to verify the nodes resolve; service refuses to accept traffic until ComfyUI `/system_stats` returns healthy and the smoke test passes.

### 4.8 Webhook dispatcher

A separate `asyncio` task (not the GPU worker) dispatches terminal-event webhooks to caller-supplied URLs. Runs in the same process; does not compete for GPU time.

#### When a webhook fires (barrier rules)

Dispatcher **never** reads a sync-mode job until the sync handler has set `webhook_handover=true`. This prevents the race where the GPU worker flips `status=completed` and the dispatcher fires before the sync handler has a chance to flush its response. The sync handler's finally block performs exactly one of these transitions atomically in SQLite:

- Response flush succeeded → `response_delivered=true, webhook_handover=true`. Dispatcher will run and **suppress** (no send).
- Response flush failed (network error, 5xx internal) → `response_delivered=false, webhook_handover=true`. Dispatcher will run and **fire** the webhook.
- Handler task cancelled before it could decide → boot recovery sets `webhook_handover=true, response_delivered=false` so the dispatcher can take over.

For `mode=async`: `webhook_handover=true` is set the moment the async 202 is written (or attempted), so the dispatcher can always proceed. `mode=sync` + no webhook supplied → dispatcher ignores the job entirely.

The full decision table:

| mode | webhook.url | response_delivered | → action |
|---|---|---|---|
| sync | null | n/a | no-op |
| sync | present | true | suppress (`webhook_delivery_status=suppressed`) |
| sync | present | false | fire |
| async | null | n/a | no-op |
| async | present | any | fire |

**Error-after-completion desync fix.** If the sync path internally fails *after* `status=completed` is persisted (e.g., presign throws), the job transitions to `status=failed, error_code=storage_error` **before** the response is flushed — this keeps the webhook payload and the HTTP response consistent. The dispatcher fires `job.failed`, not `job.completed` with a URL that didn't reach the client.

#### Terminal events dispatched (v1)

- `job.completed`
- `job.failed`
- `job.abandoned` (sync client disconnected and the job was not resumed, or shutdown grace elapsed)

No intermediate events (`running`, progress) in v1.

#### Delivery semantics — at-least-once

- 5 attempts total on jittered backoff: `15s → 1m → 5m → 15m → 1h` (total window ≈ 1h 21m).
- Each attempt gets a fresh `X-ImageGen-Delivery-Id` (uuid4); `X-ImageGen-Job-Id` stays stable so receivers dedupe on job id (§4.2 describes why dedupe state MUST be durable at the receiver).
- 2xx → `webhook_delivery_status="succeeded"`.
- 3xx → **terminal failure, no retry, no follow** (redirects are not followed — see Security below).
- 4xx (non-429) → **terminal failure, no retry** (receiver schema mismatch won't be fixed by resending).
- 429 / 5xx / network error / timeout → retry up to 5 attempts, then `"failed"`.
- On final failure, the job record flags `webhook_delivery_failed: true` (surfaced in `GET /{id}`), so the caller can fall back to polling.

**Persistence — `webhook_deliveries` table:**

```
id                    TEXT PRIMARY KEY  (uuid4 per attempt)
job_id                TEXT              -- FK → jobs.id
attempt_n             INT               -- 1..5
status_code           INT NULL          -- HTTP response code
response_body_snippet TEXT NULL         -- first 256 chars, for debug
error                 TEXT NULL         -- network/timeout error string
error_code            TEXT NULL         -- enum subset of §13 webhook codes
next_retry_at         TEXT NULL (ISO-8601)  -- NULL when terminal
created_at            TEXT
completed_at          TEXT NULL
```

Persisted queue survives restart: on boot the dispatcher scans for rows with non-terminal status and `next_retry_at <= now()`, re-queues them.

#### TOCTOU re-validation on every attempt

Host allowlist, IP range, scheme, and signing-secret availability are **re-checked before every attempt**, not just at request-validation time. Reasons:

- Admin can tighten `WEBHOOK_ALLOWED_HOSTS` via `POST /admin/reload` while a retry is pending. Stale allowlist must not leak a URL for up to ~1 h 21 m after policy change.
- Signing secret set (`WEBHOOK_SIGNING_SECRETS`) can rotate mid-window.

If a re-check fails (host removed, IP became private, secret list went empty), the delivery is marked `error_code=webhook_ssrf_blocked` or `webhook_signing_error` and terminated — no further attempts.

#### Signing (see §11 for secret management)

- Signing input: `ts + "." + body_bytes` — timestamp is included so that a replayed body without refreshed timestamp fails verification. (Stripe-style "signed timestamp" construction.)
- HMAC-SHA256 with the **first** secret in `WEBHOOK_SIGNING_SECRETS` (the currently-active signer).
- Header: `X-ImageGen-Signature: t=<unix_ts>,v1=<hex>`.
- `X-ImageGen-Timestamp: <unix_ts>` header is set to the same `ts` for receiver freshness check.
- Receivers reject deliveries where `|now - ts| > WEBHOOK_TS_SKEW_S` (default 300 s, documented in §10).
- If `WEBHOOK_SIGNING_SECRETS` is unset or empty at dispatch time, the attempt records `error_code=webhook_signing_error` and the delivery terminates without hitting the network.
- Secret rotation SOP (operator runbook): prepend new secret to the list → wait ≥ retry-window (~1h 21m) so all receivers are accepting both → remove the old secret. Senders always sign with the first entry.

#### Payload shape

```http
POST <webhook.url>
Content-Type: application/json
User-Agent: image-gen-service/0.4
X-ImageGen-Event: job.completed
X-ImageGen-Job-Id: gen_abc123
X-ImageGen-Delivery-Id: 8d3e…-uuid4
X-ImageGen-Timestamp: 1713456000
X-ImageGen-Signature: t=1713456000,v1=<hex>
<caller-supplied headers from webhook.headers, if any — Content-Type, Host, Authorization, X-ImageGen-* are reserved and cannot be overridden>

{
  "event": "job.completed",
  "job": {
    "id": "gen_abc123",
    "status": "completed",
    "created": 1713456000,
    "data": [{ "url": "https://..." }]
  }
}
```

Failure payload:

```json
{
  "event": "job.failed",
  "job": {
    "id": "gen_abc123",
    "status": "failed",
    "error": { "code": "comfy_timeout", "message": "..." }
  }
}
```

#### Security — DNS pinning, SSRF, redirects, scheme

All of these run on **every attempt**, not just request admission.

1. **Host allowlist.** If `WEBHOOK_ALLOWED_HOSTS` is set (comma-separated hosts), the URL host must be exact-match one of them. **Unset means deny-all** — safer default. In dev, operators opt in with `WEBHOOK_ALLOW_ANY_HOST=true`, which is refused if `IMAGEGEN_ENV=prod` (§11 startup assertion).
2. **Scheme.** `https` is required when `IMAGEGEN_ENV=prod`; `http` is permitted only when `IMAGEGEN_ENV=dev`.
3. **DNS pinning.** At dispatch time the dispatcher resolves the host via `socket.getaddrinfo` once, picks the first resolved IP, verifies the IP is neither private (RFC1918), loopback, link-local (169.254.0.0/16, fe80::/10), nor ULA (fc00::/7), **then connects by IP** with an explicit `Host:` header matching the original hostname. This defeats DNS-rebinding: the attacker's DNS can flip between check and connect, but we're no longer doing a hostname lookup at connect time. httpx's `transport` is configured with a custom resolver to guarantee this.
4. **Redirects.** `follow_redirects=False`. Any 3xx response is treated as terminal delivery failure (`error_code=webhook_redirect`). Receivers are expected to return 2xx at the posted URL directly; redirect chains would bypass the allowlist and IP-range check.
5. **Reserved headers.** Caller-supplied `webhook.headers` may NOT override `Content-Type`, `Host`, `Authorization`, or any `X-ImageGen-*` header. Request validation rejects reserved-key overrides with `error_code=validation_error`.
6. **Request-time rejection (§6.0):** invalid URL, wrong scheme for env, host not in allowlist, or caller-supplied reserved-header override → `400 {error_code: "validation_error"}`.

#### Startup assertion summary (§16)

- If `IMAGEGEN_ENV=prod`:
  - `WEBHOOK_ALLOWED_HOSTS` must be non-empty.
  - `WEBHOOK_ALLOW_ANY_HOST` must not be truthy.
  - At least one secret in `WEBHOOK_SIGNING_SECRETS`.
  - Refuse to boot on any violation.
- If `IMAGEGEN_ENV=dev` and `WEBHOOK_ALLOW_ANY_HOST=true`:
  - Log a prominent warning at startup; do not boot in "permissive dev" by silence.

---

## 5. Runtime topology — Docker Compose

```yaml
services:
  image-gen-service:
    build: .
    ports:
      - "127.0.0.1:8700:8000"    # bind to loopback in dev; ingress-controlled in prod
    environment:
      API_KEYS: ${API_KEYS}                      # comma-separated set (see §11)
      ADMIN_API_KEYS: ${ADMIN_API_KEYS}          # separate scope for fetch/reload
      COMFYUI_URL: http://comfyui:8188
      COMFYUI_WS_URL: ws://comfyui:8188/ws
      S3_INTERNAL_ENDPOINT: http://minio:9000
      S3_BUCKET: image-gen
      S3_ACCESS_KEY: ${MINIO_ROOT_USER}
      S3_SECRET_KEY: ${MINIO_ROOT_PASSWORD}
      IMAGE_GEN_PUBLIC_BASE_URL: ${IMAGE_GEN_PUBLIC_BASE_URL:-http://127.0.0.1:8700}  # v0.6 gateway; replaces presigned URLs
      CIVITAI_API_TOKEN: ${CIVITAI_API_TOKEN}
      VRAM_BUDGET_GB: "12"
      ASYNC_MODE_ENABLED: "false"
      IMAGEGEN_ENV: ${IMAGEGEN_ENV:-dev}                   # dev | prod — gates scheme + allowlist + assertions
      WEBHOOK_SIGNING_SECRETS: ${WEBHOOK_SIGNING_SECRETS}  # comma-separated; first = active signer
      WEBHOOK_ALLOWED_HOSTS: ${WEBHOOK_ALLOWED_HOSTS:-}    # unset = deny-all (safer default)
      WEBHOOK_ALLOW_ANY_HOST: ${WEBHOOK_ALLOW_ANY_HOST:-false}  # dev-only escape hatch; refused when IMAGEGEN_ENV=prod
      WEBHOOK_TS_SKEW_S: "300"                             # receiver-side freshness window
    volumes:
      - ./config:/app/config:ro
      - ./data:/app/data                         # SQLite job store
      - ./models:/models                         # writable for the service; full ComfyUI tree (checkpoints/, vae/, loras/)
      - ./loras:/loras                           # writable for the service
      - ./workflows:/workflows:ro
    depends_on: [comfyui, minio]
    networks: [internal]

  comfyui:
    build:
      context: ./docker/comfyui
    # No `ports` mapping in prod; only exposed on the internal network.
    # Dev override (docker-compose.override.yml) may publish 127.0.0.1:8188:8188.
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    volumes:
      # v0.5: full ComfyUI models tree from one host dir. Subdirs live at
      # ./models/{checkpoints,vae,loras}/. Read-only from ComfyUI's perspective.
      - ./models:/workspace/ComfyUI/models:ro
      - ./workflows:/workspace/ComfyUI/user/default/workflows:ro
    networks: [internal]

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    # No `ports` mapping in prod.
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-minioadmin}
    volumes:
      - ./minio-data:/data
    networks: [internal]

networks:
  internal:
    driver: bridge
```

**Dev override** (`docker-compose.override.yml`, git-ignored) may publish ComfyUI `127.0.0.1:8188` and MinIO console `127.0.0.1:9001` for local debugging — **never to `0.0.0.0` and never in prod**.

**Novita deploy.** Only `image-gen-service` gets public ingress. ComfyUI and MinIO stay on the private network. A startup assertion refuses to boot if `COMFYUI_URL` resolves to a public IP (simple IP-range check).

---

## 6. API surface

### 6.0 Input validation (applied at every endpoint that accepts these fields)

| Field | Constraint |
|---|---|
| `prompt` | `str`, 1..8000 chars |
| `negative_prompt` | `str`, 0..2000 chars |
| `model` | must exist in registry |
| `size` | regex `^\d{3,4}x\d{3,4}$`, max `width*height` ≤ `model.limits.size_max_pixels` |
| `n` | int, 1..`model.limits.n_max` |
| `steps` | int, 1..`model.limits.steps_max` |
| `cfg` | float, 0..30 |
| `seed` | int, -1 or 0..2^53 |
| `sampler` | enum from allowed ComfyUI samplers |
| `scheduler` | enum from allowed ComfyUI schedulers |
| `response_format` | `"url"` or `"b64_json"` |
| `loras[].name` | regex `^[A-Za-z0-9_][A-Za-z0-9_\-.]*$`, ≤ 20 entries |
| `loras[].weight` | float, -2..2 |
| `mode` | `"sync"` or `"async"` (rejected if `ASYNC_MODE_ENABLED=false`) |
| `webhook.url` | URL; scheme `https` when `IMAGEGEN_ENV=prod` (else `http` also allowed); host exact-match in `WEBHOOK_ALLOWED_HOSTS` (unset = deny-all unless `WEBHOOK_ALLOW_ANY_HOST=true` AND `IMAGEGEN_ENV=dev`); resolved IP must be public (non-RFC1918, non-loopback, non-link-local, non-ULA); length ≤ 2048 |
| `webhook.headers` | object, ≤ 10 entries, key matches `^[A-Za-z0-9\-_]{1,64}$`, value length ≤ 256; reserved keys cannot be overridden: `Host`, `Authorization`, `Content-Type`, `User-Agent`, any `X-ImageGen-*` |

Violations → `400` with `error_code="validation_error"` and a field pointer.

### 6.1 `POST /v1/images/generations` (sync — default)

OpenAI-compatible. Blocks until the image is ready or the job fails.

Request body — see §6.0 for field rules. Response headers include `X-Job-Id: gen_<ksuid>` (useful if the client disconnects and wants to re-poll).

Response (200):
```json
{
  "created": 1713456000,
  "data": [{ "url": "http://127.0.0.1:8700/v1/images/gen_abc123/0.png" }]
}
```

The URL points at **our** `/v1/images/{job_id}/{index}.png` gateway (see §6.X), not at S3 directly. Bearer auth required on the GET. Dev emits `http://…`; prod ingress should expose `https://…` via `IMAGE_GEN_PUBLIC_BASE_URL`.

### 6.1.1 `GET /v1/images/{job_id}/{index}.png` — image fetch gateway (v0.6)

Streams the generated PNG back to the caller. Auth: `require_auth` (either generation or admin scope).

- **Path parameters:** `job_id` must match `gen_<ksuid>`; `{index}.png` must match `^\d+\.png$` (index out of range → 404).
- **Status:** 200 on success with `Content-Type: image/png` + PNG bytes; 401 on missing/invalid Bearer; 404 on unknown job, unfetched-yet-job, out-of-range index, or missing S3 object; 502 on S3 transport failure.
- **Response body on error:** standard envelope `{"error":{"code":...,"message":...}}`. Image bytes never included in error responses.
- **Caching:** response carries no `Cache-Control`; callers SHOULD treat fetches as authed resources (not CDN-safe).

### 6.2 `POST /v1/images/generations` with `"mode": "async"` (extension — feature-flagged)

Returns immediately **only if `ASYNC_MODE_ENABLED=true`**. While false (default in v1), the field is rejected with `400 {error_code: "async_not_enabled"}`.

Optional webhook object — caller gets a push notification on terminal state instead of (or in addition to) polling:

```json
{
  "model": "noobai-xl-vpred-1",
  "prompt": "...",
  "mode": "async",
  "webhook": {
    "url": "https://loreweave.internal/v1/webhooks/image-gen",
    "headers": { "X-LoreWeave-Env": "prod" }
  }
}
```

Webhook is **additive, not exclusive** — the caller still gets `202 { id }` and can poll `GET /{id}`. Webhook is best-effort push; poll is authoritative. Semantics and retry policy: see §4.8.

**Sync + webhook combination.** The `webhook` object is also accepted on sync requests. If the sync response is delivered successfully, the webhook is suppressed (`webhook_delivery_status="suppressed"`). If the sync client disconnects before the response is written, the webhook fires as a fallback — this hedges against client-side timeouts (e.g. LoreWeave's HTTP client giving up before a 90 s Chroma generation finishes).

**Async-request-never-reached-client.** If a client disconnects before receiving the `202 { id }` body (TCP drops after submit), the job is still persisted and the webhook will still fire on terminal transition — the receiver may get a payload for a job id the submitting client never learned. Receivers MUST tolerate this (treat unknown `job_id` as a new terminal state to persist, not an error).

Response (202) for pure async:
```json
{ "id": "gen_abc123", "status": "processing" }
```

### 6.3 `GET /v1/images/generations/{id}`

Response (200):
```json
{
  "id": "gen_abc123",
  "status": "completed",
  "data": [{ "url": "..." }],
  "webhook_delivery_status": "succeeded"
}
```

Statuses: `queued` → `running` → `completed` | `failed` | `abandoned` (sync client disconnected).
`webhook_delivery_status` is one of `null | pending | succeeded | failed | suppressed` (null when no webhook was supplied).
Failed response includes `error: { code, message }` with `code` from the §13 enum.

### 6.4 `GET /v1/models`

OpenAI-compatible plus `capabilities` + `backend`:

```json
{
  "object": "list",
  "data": [
    {
      "id": "noobai-xl-vpred-1",
      "object": "model",
      "created": 1713000000,
      "owned_by": "local",
      "capabilities": { "image_gen": true },
      "backend": "comfyui"
    }
  ]
}
```

### 6.5 `GET /v1/loras`

Lists LoRAs (name, sidecar metadata, sha256). No auth scope escalation.

### 6.6 `POST /v1/loras/fetch` (admin scope required)

Downloads from Civitai per §4.5 hardening rules. Returns the final sidecar.

### 6.7 `GET /health`

Returns a deep health probe:

```json
{
  "status": "ok",
  "comfyui": "ok",
  "s3": "ok",
  "db": "ok"
}
```

HTTP status is `200` when all are `ok`, **`503`** when any critical dependent is down. This makes `/health` a real probe (matches Docker healthcheck semantics); `GET /v1/models` remains a light probe that just checks the registry loaded.

In prod, gate the verbose shape behind auth; unauthenticated callers get a boolean-only `{"status":"ok"}` (reveals nothing about topology).

### 6.8 `POST /admin/reload` (admin scope required)

Replaces SIGHUP. Re-reads `config/models.yaml`, runs startup validation, atomically swaps the registry. Responds 409 if validation fails (registry unchanged).

### 6.9 `GET /v1/webhooks/deliveries/{job_id}` (admin scope required)

Returns the delivery attempt log for a job — useful for debugging "why didn't LoreWeave receive my webhook":

```json
{
  "job_id": "gen_abc123",
  "webhook_url": "https://loreweave.internal/v1/webhooks/image-gen",
  "webhook_delivery_status": "failed",
  "attempts": [
    { "attempt_n": 1, "status_code": 503, "response_body_snippet": "...", "next_retry_at": "2026-04-18T10:00:15Z", "error": null },
    { "attempt_n": 2, "status_code": null, "response_body_snippet": null, "next_retry_at": "2026-04-18T10:01:15Z", "error": "connection timeout" },
    { "attempt_n": 5, "status_code": 502, "response_body_snippet": "...", "next_retry_at": null, "error": null }
  ]
}
```

---

## 7. Request lifecycle

```
Client ──▶ FastAPI /v1/images/generations
              │
              ├─ auth (multi-key, constant-time, key-id logged)
              ├─ validate (§6.0)
              ├─ persist Job row (SQLite) — status=queued
              ├─ enqueue
              │
           ┌──▼─── Worker ───────────────────┐         ┌── Webhook dispatcher ──┐
           │  pull job                       │         │  (separate asyncio task) │
           │  status=running, persist        │         │                          │
           │  resolve model → workflow file  │         │  on terminal transition: │
           │  inject LoRAs (§9)              │         │    if webhook_url and    │
           │  submit → ComfyUI POST /prompt  │         │       (async OR sync-    │
           │  subscribe WS /ws?clientId=...  │         │        disconnected):    │
           │  (fallback: poll /history)      │         │      enqueue delivery    │
           │  fetch PNG bytes via /view      │         │                          │
           │  upload to S3 (retry)           │         │  dispatcher loop:        │
           │  presign URL                    │─ done ─▶│    sign HMAC-SHA256      │
           │  status=completed, persist      │         │    POST to webhook.url   │
           │  resolve sync future (if live)  │         │    2xx → succeeded       │
           │  mark response_delivered=true   │         │    4xx → failed (no retry)│
           │  (if sync reply succeeded)      │         │    429/5xx/network →     │
           └─────────────────────────────────┘         │      schedule retry      │
                                                       │    after 5 attempts →    │
                                                       │      status=failed,      │
                                                       │      flag job record     │
                                                       └──────────────────────────┘

 Sync mode:    asyncio.shield wraps the future await;
               Request.is_disconnected() listener → flip mode="async",
               job keeps running, X-Job-Id already returned.
               If webhook supplied and sync response never delivered,
               dispatcher fires the webhook (see §4.8).

 Async mode:   client receives 202 immediately, polls GET /{id}
               AND/OR receives webhook on terminal transition.

 Restart:      boot scan marks any running→failed (error_code=service_restarted);
               queued→re-enqueued; webhook_deliveries with non-terminal status and
               next_retry_at <= now → re-enqueued into dispatcher.

 Orphan reap:  completed jobs whose result was never fetched within
               ORPHAN_REAPER_TTL → delete their S3 objects (keep the DB row).
```

---

## 8. Model roster — day 1

| Slot | Model | Backend | Workflow | Approx VRAM | Role |
|---|---|---|---|---|---|
| 1 | **NoobAI-XL v1.1** (eps) | ComfyUI | `sdxl_eps.json` | ~7 GB | Anime / stylized / uncensored. Largest active LoRA ecosystem on Civitai. Default. |
| 2 | **Chroma1-HD (Q8 GGUF)** | ComfyUI | `chroma_gguf.json` | ~9 GB | Photoreal / long-prompt (T5). Quantized to stay under budget. Requires custom nodes (§4.7). |

VRAM envelope: target ≤ 12 GB peak. Both slots fit individually with headroom; not loaded simultaneously.

**Model-swap cost.** When `job.model_name` differs from the last job's, the adapter calls `POST /free {unload_models: true, free_memory: true}` on ComfyUI before submitting, so VRAM does not double-occupy. Swap cost is one checkpoint reload (~5–20 s depending on model); logged as a distinct metric (§13).

Post-v1 candidates (add a YAML entry only): Illustrious merges (Nova Anime, JANKU), Chroma full-precision, SD 3.5 Large.

---

## 9. LoRA management — names, sidecars, and graph injection

**Storage & safety:**
- Single directory `./loras/`, service-writable, ComfyUI-read-only.
- Names restricted: `^[A-Za-z0-9_][A-Za-z0-9_\-.]*$`. No slashes, no leading dot, no `..`.
- Every request LoRA is resolved to `os.path.realpath` and verified `Path.is_relative_to("./loras/")` before submission.

**Sidecar format (`<name>.json`):**

```json
{
  "name": "my_style",
  "filename": "my_style.safetensors",
  "sha256": "abc...",
  "source": "civitai",
  "civitai_model_id": 12345,
  "civitai_version_id": 67890,
  "base_model_hint": "SDXL 1.0",
  "trigger_words": ["my_style"],
  "fetched_at": "2026-04-18T..."
}
```

**Workflow anchor convention.** Workflow JSONs must contain specially-tagged nodes so the adapter can find insertion points by meta title rather than by hardcoded node id:

| Anchor (`_meta.title`) | Purpose |
|---|---|
| `%MODEL_SOURCE%` | Node whose output (slot 0) is the MODEL (e.g. `CheckpointLoaderSimple` for SDXL, `UnetLoaderGGUF` for Chroma) |
| `%CLIP_SOURCE%` | Node whose output (slot 0 or 1 depending on loader) is the CLIP |
| `%LORA_INSERT%` | Where the LoRA chain inserts — same as `%MODEL_SOURCE%` and `%CLIP_SOURCE%` for simple cases, or a dedicated pass-through node |
| `%POSITIVE_PROMPT%` | `CLIPTextEncode` node whose `text` field receives the prompt |
| `%NEGATIVE_PROMPT%` | `CLIPTextEncode` node whose `text` field receives the negative prompt |
| `%KSAMPLER%` | The `KSampler` (so we can overwrite `seed`, `steps`, `cfg`, `sampler_name`, `scheduler`) |
| `%OUTPUT%` | `SaveImage` whose outputs we harvest after completion |

Startup validation refuses to load any workflow missing required anchors.

**LoRA injection algorithm:**

```
let A = find_anchor("%MODEL_SOURCE%")
let B = find_anchor("%CLIP_SOURCE%")   # may equal A for checkpoint loaders
let next_id = max(node_ids) + 1
let model_ref = [A.id, 0]
let clip_ref  = [B.id, 1 if B is CheckpointLoader else 0]

for lora in request.loras:
    node = {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": lora.name,
            "strength_model": lora.weight,
            "strength_clip":  lora.weight,
            "model": model_ref,
            "clip":  clip_ref,
        },
        "_meta": {"title": f"lora:{lora.name}"}
    }
    graph[str(next_id)] = node
    model_ref = [str(next_id), 0]
    clip_ref  = [str(next_id), 1]
    next_id += 1

# Rewrite downstream consumers of A/B to use the final model_ref / clip_ref.
for each node that references A/B as model/clip:
    replace (A.id, slot) → model_ref  for .inputs.model
    replace (B.id, slot) → clip_ref   for .inputs.clip
```

`%MODEL_SOURCE%` != `%CLIP_SOURCE%` is handled (Chroma/FLUX has `UnetLoaderGGUF` for model and `DualCLIPLoader` for clip).

**vpred injection** (deferred per v0.5 — no day-1 model uses v-prediction). If `model_cfg.prediction == "vpred"`, insert a `ModelSamplingDiscrete` node with `sampling="v_prediction"`, `zsnr=true` between `%MODEL_SOURCE%` and the LoRA chain — added to the anchor algorithm as a prepend step. Re-activate only if a future model brings back v-prediction.

**Output collection.** After completion, iterate `history[prompt_id].outputs`; for each node whose `_meta.title == "%OUTPUT%"`, take its `images[]` array.

---

## 10. Integration guide amendments

The current [EXTERNAL_AI_SERVICE_INTEGRATION_GUIDE.md §6](../EXTERNAL_AI_SERVICE_INTEGRATION_GUIDE.md) specifies only the sync image-gen contract. We will land a separate PR to the guide (owner: @letuhao1994) that **adds** (strictly additive):

1. **Async mode** on `/v1/images/generations` mirroring §7 (video) — field `"mode": "async"` → `202 Accepted { id, status }`, `GET /v1/images/generations/{id}` for polling.
2. **Optional request fields** used by local backends: `steps`, `cfg`, `seed`, `sampler`, `scheduler`, `negative_prompt`, `loras[]`.
3. **Optional `webhook` object** on both sync and async requests — see §4.8 and §6.2.
4. **Webhook receiver contract (mandatory for any service consuming webhook deliveries).**
5. **Suggested LoreWeave receiver route** `POST /v1/webhooks/image-gen` so services know where to point `webhook.url`. Route scope: authenticated by signature header only (no bearer), idempotent, commits job result to LoreWeave's MinIO and marks the user's generation as completed.
6. Note that presigned URLs are short-lived (default 1 h) and that LoreWeave should download within `PRESIGN_TTL_S`.

#### Webhook receiver contract (amendment content)

**Signature header format:** `X-ImageGen-Signature: t=<unix_ts>,v1=<hex>` (hex is lowercase, no prefix). The `t=` value must equal `X-ImageGen-Timestamp`; receivers MAY rely on either.

**Signing input:** `ts + "." + raw_body` where `raw_body` is the exact bytes of the HTTP body — NOT a re-serialization. Verify **before** JSON parsing:

```go
// LoreWeave (Go) receiver reference implementation

import (
    "crypto/hmac"
    "crypto/sha256"
    "encoding/hex"
    "io"
    "net/http"
    "strconv"
    "strings"
    "time"
)

func VerifyImageGenWebhook(r *http.Request, acceptedSecrets []string, skewSeconds int64) ([]byte, error) {
    rawBody, err := io.ReadAll(r.Body)
    if err != nil {
        return nil, err
    }

    sigHeader := r.Header.Get("X-ImageGen-Signature")      // "t=1713456000,v1=abcdef..."
    var tsPart, vPart string
    for _, p := range strings.Split(sigHeader, ",") {
        if strings.HasPrefix(p, "t=") { tsPart = p[2:] }
        if strings.HasPrefix(p, "v1=") { vPart = p[3:] }
    }
    if tsPart == "" || vPart == "" {
        return nil, errMalformedSignature
    }

    ts, err := strconv.ParseInt(tsPart, 10, 64)
    if err != nil { return nil, err }
    if abs(time.Now().Unix() - ts) > skewSeconds {
        return nil, errStaleSignature               // replay window exceeded
    }

    received, err := hex.DecodeString(vPart)
    if err != nil { return nil, err }

    input := append([]byte(tsPart + "."), rawBody...)
    for _, secret := range acceptedSecrets {          // accept any rotated secret
        mac := hmac.New(sha256.New, []byte(secret))
        mac.Write(input)
        if hmac.Equal(mac.Sum(nil), received) {       // constant-time compare
            return rawBody, nil
        }
    }
    return nil, errBadSignature
}
```

**Critical receiver rules:**

- **Verify over raw body bytes, BEFORE parsing.** A receiver that does `json.Unmarshal` first and then re-serializes for HMAC will fail verification even on legitimate deliveries (field ordering, whitespace, escape differences). If using `gin.Context.ShouldBindJSON` or similar, capture `c.GetRawData()` first and verify on that.
- **Use `hmac.Equal` (Go) / `hmac.compare_digest` (Python) / `crypto.timingSafeEqual` (Node) for the comparison.** Never `==` or `bytes.Equal` — those leak length via early exit.
- **Freshness window.** Reject if `|now - ts| > 300 s`. This caps the replay window even if the signing secret later leaks.
- **Accept any secret from a rotated set.** Our sender always signs with the first entry in `WEBHOOK_SIGNING_SECRETS`; receivers must accept any entry to survive rotation.
- **Durable dedupe by `X-ImageGen-Job-Id`.** Persist seen job ids (SQL/KV) — in-memory dedupe loses consistency on receiver restart and double-processes. At-least-once semantics (see §4.2) GUARANTEE duplicates during crash/retry windows.
- **Respond 2xx within 10 s of persisting the delivery.** Do not wait for downstream pipelines. Slow receivers cause sender retry storms.
- **Treat unknown `event` types as no-ops.** Forward-compatibility: future events must not 5xx legacy receivers.
- **Tolerate unknown `job_id`.** The submitting client may have disconnected before receiving the `202 { id }`, so the receiver may see a job id the caller never learned — persist it as a new terminal state rather than rejecting.
- **Do NOT follow 3xx responses you receive from your own upstreams on the sender side.** (Applies to the dispatcher — not relevant to receivers.)

#### Ordering constraint

- Async mode MUST NOT be enabled in prod (`ASYNC_MODE_ENABLED=true`) until the guide PR is merged **and** LoreWeave's OpenAI-fallback adapter is confirmed to handle the 202+poll case.
- Webhook delivery is additive and can be used with sync mode (as disconnect-fallback) even before async ships, **provided** LoreWeave exposes the `POST /v1/webhooks/image-gen` receiver and implements the verification contract above.

---

## 11. Security

- **Auth — multi-key set.**
  - `API_KEYS` (comma-separated) — the accepted generation-scope keys.
  - `ADMIN_API_KEYS` (comma-separated, disjoint) — required for `POST /v1/loras/fetch` and `POST /admin/reload`.
  - Keys are labelled by a short `kid` (first 8 chars of SHA-256); the kid is logged on every auth event for rotation attribution.
  - Comparison is constant-time (`hmac.compare_digest`).
  - Rotation SOP: add new key to the set → LoreWeave cuts over → remove old key from the set (single env change + reload).

- **Prod network exposure.**
  - Only `image-gen-service:8000` is public. ComfyUI and MinIO live on the `internal` network with no published ports.
  - Startup assertion: the service resolves `COMFYUI_URL` and `S3_INTERNAL_ENDPOINT` hosts; if either resolves to a public (non-RFC1918, non-loopback) address, the service refuses to boot.
  - Dev override file is git-ignored and may expose `127.0.0.1:8188`/`127.0.0.1:9001` only.

- **Civitai fetch.** See §4.5 hardening rules 1–11. Key points repeated:
  - Host allowlist + `.safetensors` only + SHA-256 verification against Civitai-advertised hash + file-size cap + total-dir ceiling + admin scope + per-URL lock.
  - `safetensors` format is chosen over pickle; it is a necessary but not sufficient defense — hash pinning is load-bearing.

- **LoRA volume write boundary.** Only `image-gen-service` has write access to `./loras/`. ComfyUI mounts it `:ro`, so a compromised workflow cannot plant a trojan LoRA for a later run.

- **Prompt-driven resource exhaustion.** Bounded per model via `limits.*` in YAML + validation in §6.0. Unbounded `n`/`steps`/`size` is rejected at the API boundary.

- **Secret management.** v1 reads all secrets from env. Compose production mode supports Docker secrets via `*_FILE` variants (`API_KEYS_FILE`, `MINIO_ROOT_PASSWORD_FILE`, `CIVITAI_API_TOKEN_FILE`) — the service reads the file if the `*_FILE` var is set, otherwise falls back to the plain var.

- **Presigned URL handling.** Signed URLs and `X-Amz-Signature` are redacted from all logs. Signed URLs appear only in successful response bodies, never in error paths.

- **Webhook signing secrets (multi-secret set).** `WEBHOOK_SIGNING_SECRETS` is comma-separated (first entry is the active signer; receivers accept any). Stored alongside `API_KEYS` / `ADMIN_API_KEYS` (env or `*_FILE` variant for Docker secrets). **Separate blast radius:** leaking this secret lets an attacker forge inbound-to-LoreWeave payloads but not authenticate to this service. Zero-downtime rotation SOP:
  1. Prepend new secret to `WEBHOOK_SIGNING_SECRETS`; operators distribute it to all receivers via the same mechanism.
  2. Wait ≥ retry-window (~1 h 21 m) so every in-flight delivery's signature is accepted even if the receiver saw only the old secret.
  3. Remove the old secret from the list.

- **Webhook SSRF — layered defense.** Each layer is required; none replaces the others.
  1. **Scheme gate:** `IMAGEGEN_ENV=prod` ⇒ `https` only. `http` allowed only when `IMAGEGEN_ENV=dev`.
  2. **Host allowlist (fail-closed).** `WEBHOOK_ALLOWED_HOSTS` unset = **deny-all**. Dev operators may set `WEBHOOK_ALLOW_ANY_HOST=true`, which is **refused at startup** if `IMAGEGEN_ENV=prod`. This inversion (deny-by-default) prevents prod shipping with an accidentally-blank allowlist.
  3. **IP-range filter.** At dispatch time, the resolved IP must be public — not RFC1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback (`127.0.0.0/8`, `::1`), link-local (`169.254.0.0/16`, `fe80::/10`), or ULA (`fc00::/7`). This blocks AWS IMDS (`169.254.169.254`), internal-network probes, and localhost abuse. A dev flag (`WEBHOOK_ALLOW_PRIVATE=true`) is available only when `IMAGEGEN_ENV=dev`.
  4. **DNS pinning.** Resolve once at dispatch, verify the IP, then connect **by IP** with explicit `Host:` header. Defeats DNS rebinding (attacker flips A-record between check and connect).
  5. **No redirect follow.** `follow_redirects=False`; any 3xx is terminal failure. Following would bypass layers 1–4.
  6. **TOCTOU re-validation.** Every retry attempt re-runs layers 1–3 (policy may have tightened).
  7. **Startup assertion.** In `IMAGEGEN_ENV=prod`, refuse to boot if allowlist is empty, `WEBHOOK_ALLOW_ANY_HOST=true`, or `WEBHOOK_SIGNING_SECRETS` is empty.

- **Webhook URL logging.** Webhook URLs (including caller-supplied headers) are logged at `INFO`. Signatures (`X-ImageGen-Signature`) and signing secrets are **never** logged at any level. Caller-supplied headers are logged verbatim — callers should not put secrets in `webhook.headers`.

- **`IMAGEGEN_ENV` is load-bearing.** Must be set to `prod` or `dev`; any other value (or unset) refuses boot. Logged at startup alongside the allowlist + signing-set fingerprint so operators see the posture unambiguously.

- **Prompt content policy.** Out of scope; upstream (LoreWeave) owns any acceptable-use policy. The service does not classify or moderate prompts.

- **Health-endpoint info leak.** Unauthenticated `/health` returns `{status: ok|degraded}` only. Authenticated callers (either scope) get the verbose shape.

- **Data at rest — jobs table (v0.6 posture note).** The `jobs` SQLite table stores `input_json` (caller's full POST body, including `prompt`) in plaintext. Logging redacts prompts (`LOG_PROMPTS` gate + redaction processor) but the on-disk SQLite file does not. An attacker with filesystem access to `./data/jobs.db` can read every prompt ever generated. Mitigations in scope for v1: restrict filesystem perms on the mounted volume; rely on volume encryption at the host level. Out of scope for v1: application-level encryption of prompts. Revisit if LoreWeave sends PII-grade content.

- **Image-fetch gateway auth (v0.6).** `GET /v1/images/{job_id}/{index}.png` requires a valid Bearer key (either scope) — no per-key job ownership check. A compromised generation key can enumerate any job's images by iterating `gen_<ksuid>` values. ksuid is 128-bit so brute-force enumeration is impractical, but a leaked key + access to our audit logs (which include `X-Job-Id`) is enough to pull arbitrary images. Mitigation: rotate keys promptly; the `kid` in logs scopes blast-radius auditing. Revisit if multi-tenant posture is adopted.

---

## 12. Concurrency / resource model

- One GPU worker task, one job at a time. Queue capacity `MAX_QUEUE` (default 20) counts **queued + running**; depth > `MAX_QUEUE` → `429`.
- **Webhook dispatcher** is a separate asyncio task and does not share the GPU-worker slot — it only does HTTP + HMAC + SQLite writes. Bounded in-flight deliveries via `WEBHOOK_MAX_IN_FLIGHT` (default 8) to avoid head-of-line blocking when a receiver is slow.
- **Sync/dispatcher barrier.** Dispatcher never reads a sync-mode job until `webhook_handover=true` is committed by the sync handler (§4.2 + §4.8). This prevents the race where the GPU worker writes `status=completed` and the dispatcher fires before the sync handler has had a chance to flush its response. The barrier is a SQLite boolean, not an in-memory flag — restart-safe.
- Per-job hard cap `JOB_TIMEOUT_S` (default 300). On timeout the adapter calls `/interrupt` + (if applicable) `DELETE /queue`, then `/free` to release VRAM, then verifies `/system_stats` before next job.
- Per-webhook-attempt HTTP timeout `WEBHOOK_HTTP_TIMEOUT_S` (default 10); dispatcher treats timeout as a retriable error.
- Model swap triggers an explicit unload (§8).
- Graceful shutdown: stop accepting new jobs, wait up to `SHUTDOWN_GRACE_S` (default 90) for the active GPU job, flush MinIO uploads; drain the in-flight webhook attempts (they complete normally or get persisted as pending for next boot); any still-running job gets `status=abandoned, error_code=service_stopping` on restart boot scan.

**VRAM guard.** At dispatch time the router checks `model_cfg.vram_estimate_gb + sum(lora_weights_estimate) ≤ VRAM_BUDGET_GB`; refusal is `error_code=vram_budget_exceeded`. LoRA overhead is estimated at 64 MB per LoRA (coarse; good enough to catch "20 LoRAs stacked" mistakes).

Scaling later: set `WORKERS=N` and run one ComfyUI sidecar per worker with GPU affinity; or move to a ComfyUI cluster behind a load balancer. Not in v1.

---

## 13. Observability

- **Structured JSON logs**, one per request + one per job-state-change. Correlation id = `job.id`.
- **Fields logged**: ts, level, event, job_id, key_id, model, prompt_id (ComfyUI), queue_wait_ms, gen_ms, upload_ms, total_ms, n_loras, outcome, error_code.
- **Redaction**: prompts are logged at `DEBUG` only (opt-in via `LOG_PROMPTS=true`); presigned URLs and full LoRA download URLs are **never** logged at any level.

- **Error code enum** (required on every failed job and 4xx/5xx response):

| Code | Meaning |
|---|---|
| `validation_error` | Request failed §6.0 rules |
| `async_not_enabled` | `mode=async` while feature-flagged off |
| `queue_full` | Backpressure — retry later |
| `comfy_unreachable` | ComfyUI connection refused or WS/HTTP dead |
| `comfy_error` | ComfyUI returned `node_errors` or execution error |
| `comfy_timeout` | `JOB_TIMEOUT_S` elapsed mid-generation |
| `storage_error` | MinIO upload failed after retries |
| `lora_missing` | Referenced LoRA not present on disk |
| `lora_fetch_error` | Civitai fetch failed (downstream returned non-2xx, hash mismatch, size cap) |
| `vram_budget_exceeded` | Router rejected by VRAM guard |
| `service_restarted` | Running job found during boot scan |
| `service_stopping` | Shutdown grace window elapsed |
| `auth_error` | Missing/invalid/wrong-scope key |
| `not_found` | Unknown model name or job id |
| `webhook_delivery_failed` | All 5 delivery attempts exhausted without a 2xx |
| `webhook_signing_error` | `WEBHOOK_SIGNING_SECRETS` empty at dispatch time (misconfig) |
| `webhook_ssrf_blocked` | Resolved IP was private/loopback/link-local/ULA; or host not in allowlist at re-validation |
| `webhook_redirect` | Receiver returned 3xx (not followed — terminal failure) |
| `internal` | Unclassified 5xx |

- **Audit log** (separate stream, `audit.jsonl`): every auth event (accepted + rejected), every LoRA fetch (attempted + completed, with model_id, version_id, sha256, size, upstream status), every admin call (reload, fetch), every webhook delivery attempt (job_id, url, attempt_n, status_code, duration_ms, outcome — **not the signature**). Retention governed by `AUDIT_LOG_RETENTION_DAYS` (default 90).

- **Metrics (logs-derived for v1)**: per-model counts / latencies / error-code breakdown. Prometheus / OpenTelemetry integration deferred to v2.

---

## 14. Dependencies

Pinned in `pyproject.toml`:

```
python = ">=3.11,<3.13"
fastapi = ">=0.115,<0.116"
uvicorn[standard] = ">=0.32,<0.33"
pydantic = ">=2.9,<3"
httpx = ">=0.27,<0.28"         # async HTTP — ComfyUI + Civitai
websockets = ">=13,<14"        # ComfyUI WS completion signal
boto3 = ">=1.35,<2"            # S3/MinIO
aioboto3 = ">=13,<14"          # optional async uploads
PyYAML = ">=6.0.2"
python-multipart = "*"         # FastAPI form parsing
svix-ksuid = ">=0.6.2"         # lexicographically-sortable IDs
watchfiles = ">=0.24"          # Windows-safe config reload (+ /admin/reload)
aiosqlite = ">=0.20"           # async SQLite driver
tenacity = ">=9"               # retry policy for S3 uploads

# dev
ruff = "*"
pytest = "*"
pytest-asyncio = "*"
respx = "*"                    # httpx mocking
```

Lock via `uv` or `pip-compile`. CI pins exact resolutions.

---

## 15. Repo layout (planned)

```
local-image-generator-service/
├── app/
│   ├── main.py                 # FastAPI entry + startup checks
│   ├── api/
│   │   ├── images.py           # /v1/images/generations
│   │   ├── models.py           # /v1/models
│   │   ├── loras.py            # /v1/loras, /v1/loras/fetch
│   │   ├── admin.py            # /admin/reload
│   │   └── health.py
│   ├── queue/
│   │   ├── worker.py           # GPU worker
│   │   ├── jobs.py
│   │   └── store.py            # SQLite layer (jobs + webhook_deliveries)
│   ├── webhooks/
│   │   ├── dispatcher.py       # separate asyncio task loop
│   │   ├── signing.py          # HMAC-SHA256
│   │   └── retry.py            # 5-attempt schedule
│   ├── backends/
│   │   ├── base.py             # Protocol
│   │   ├── comfyui.py          # HTTP + WS + /view + /interrupt + /free
│   │   ├── diffusers.py        # stub
│   │   └── remote_api.py       # stub
│   ├── registry/
│   │   ├── models.py           # YAML loader + startup validation
│   │   └── workflows.py        # anchor resolver + LoRA injector
│   ├── loras/
│   │   ├── scanner.py
│   │   └── civitai.py          # host allowlist, hash verify, lock, audit
│   ├── storage/
│   │   └── s3.py               # two clients (internal + public)
│   ├── auth.py                 # multi-key + kid + scopes
│   └── validation.py           # Pydantic models
├── config/
│   └── models.yaml
├── docker/
│   └── comfyui/
│       ├── Dockerfile          # pinned ComfyUI + custom nodes
│       └── custom-nodes.txt    # pin list
├── workflows/
│   ├── sdxl_vpred.json
│   └── chroma_gguf.json
├── scripts/
│   └── pull-models.sh          # HuggingFace pre-download helper
├── tests/
├── docker-compose.yml
├── docker-compose.override.yml.example
├── Dockerfile                  # image-gen-service
├── pyproject.toml
└── docs/
    └── architecture/
        └── image-gen-service.md   # (this file)
```

---

## 16. Startup checks & validation

On boot, before accepting traffic:

1. Load `config/models.yaml`; verify every `checkpoint`, `vae`, `clip_l`, `t5xxl`, `workflow` file exists.
2. Parse each workflow JSON; verify required anchor nodes (§9) present.
3. Open SQLite, run migrations, reconcile running/queued jobs (§4.2) and pending webhook deliveries (§4.8).
4. Resolve `COMFYUI_URL` + `S3_INTERNAL_ENDPOINT` — refuse to start if either is a public IP.
5. Probe ComfyUI `/system_stats` — wait up to `COMFY_BOOT_WAIT_S` (default 60 s) for it to come up.
6. Run a **smoke test**: for each registered model, submit a 1-step 256×256 prompt with a fixed seed; confirm completion within 120 s. Failure → refuse to boot.
7. Probe MinIO `HeadBucket`; create bucket if missing.
8. **`IMAGEGEN_ENV` validation.** Must be exactly `prod` or `dev`; otherwise refuse to boot. The resolved value gates assertions 9 and 10.
9. **Prod posture assertions.** If `IMAGEGEN_ENV=prod`:
   - `WEBHOOK_ALLOWED_HOSTS` must be non-empty → else refuse to boot.
   - `WEBHOOK_ALLOW_ANY_HOST` must be false/unset → else refuse to boot.
   - `WEBHOOK_ALLOW_PRIVATE` must be false/unset → else refuse to boot.
   - `WEBHOOK_SIGNING_SECRETS` must have at least one entry → else refuse to boot.
   - All resolved webhook allowlist hosts must currently resolve to public IPs → warn (not fail) if any is unresolvable (DNS may recover).
10. **Dev posture warnings.** If `IMAGEGEN_ENV=dev`:
   - If `WEBHOOK_ALLOW_ANY_HOST=true` → prominent startup log line: "webhook allowlist is permissive (dev mode)".
   - If `WEBHOOK_SIGNING_SECRETS` is empty → warn that any webhook dispatch will terminate with `webhook_signing_error`.
11. **Posture fingerprint.** Emit `startup_ok` audit event including: `imagegen_env`, allowlist hash, signing-secret-count, api-key-id set fingerprint. Gives operators one line to confirm deploy posture.

Failures at any step produce a clear `startup_failed{stage, reason}` log and a non-zero exit.

---

## 17. Open questions / deferred

1. **Model pre-download UX.** `scripts/pull-models.sh` wraps `huggingface-cli`, reading the list from `config/models.yaml`. Exact HF repo paths per model TBD in BUILD.
2. **Progress streaming.** ComfyUI's WS emits per-node `progress` events. v1 does not surface to callers; consider SSE streaming on sync mode later.
3. **Image-to-image / inpaint / ControlNet.** Same endpoint shape + extra fields. Deferred to v2.
4. **Per-caller quota.** v1 uses shared keys, so global queue bound is the only throttle. When multi-tenant arrives, add a key-scoped token bucket.
5. **Prometheus / OTel.** Logs-derived metrics for v1; structured pipeline deferred.
6. **Hash-algorithm flexibility for Civitai.** Currently SHA-256 only; Civitai exposes BLAKE3 and AutoV2 too — add opportunistic multi-hash verify later.
7. **Sidecar `.json` trust.** Is the sidecar read back as authoritative metadata, or re-verified against Civitai on each use? v1 reads once at fetch time; re-verify is a future hardening.
8. **Zero-downtime reload.** `/admin/reload` swaps the registry atomically but cannot change the ComfyUI sidecar's custom-node set. Changing pinned nodes is a redeploy.

---

## 18. Verification checklist

- [x] All 4-perspective review HIGHs addressed in v0.2 (see §20 Change log for mapping)
- [x] All MEDs addressed or explicitly deferred with rationale
- [x] No placeholders / TBDs in core contract
- [x] Consistent with integration guide + amendments explicitly listed
- [x] VRAM math stated; VRAM guard enforced; model-swap cost named
- [x] Every component has clear ownership + failure mode
- [x] Error code enum and audit log defined
- [x] Prod network posture explicit (startup assertion)
- [x] Civitai threat model explicit (11-point hardening)
- [x] ComfyUI API contract full (WS, client_id, /view anchor lookup, /interrupt + /free + /queue cancel)
- [x] SQLite persistence for restart recovery
- [x] Sync-disconnect + orphan-reaper story
- [x] Webhook delivery: component, retry policy, persistence, signing, allowlist, sync+webhook fallback semantics, restart recovery (v0.3)
- [x] Webhook hardening: DNS pinning, IP-range SSRF block, no-redirect, multi-secret rotation, anti-replay signing, TOCTOU re-validation, sync/dispatcher barrier, fail-closed allowlist, explicit IMAGEGEN_ENV mode switch, Go receiver reference implementation (v0.4)

---

## 19. Glossary / references

- **Prompt-API format:** ComfyUI's `{node_id: {class_type, inputs, _meta}}` JSON accepted by `POST /prompt`. Distinct from the workflow-editor JSON shown in the UI.
- **Anchor node:** a node carrying `_meta.title = "%...%"` so the adapter can find it by role, not by id.
- **vpred (v-prediction):** alternative model-prediction parameterisation used by NoobAI-XL Vpred; requires `ModelSamplingDiscrete` injection with `zsnr=true`.
- **SigV4 host binding:** AWS-style request signing includes the `Host` header; mismatched signing vs. serving hosts break the signature.

---

## 20. Change log

### v0.6 (2026-04-19) — Cycle 3 amendment: backend gateway replaces presigned URLs

Cycle 3 CLARIFY Q4 redirected the S3 access model. v0.4/v0.5 mandated two boto3 clients (internal for upload, public for presign) and returned S3-presigned URLs directly to the caller. v0.6 collapses this to a single internal client + a backend-gateway endpoint.

**Contract changes (§4.6, §6.1, §6.X new):**
- `data[].url` now points at `{IMAGE_GEN_PUBLIC_BASE_URL}/v1/images/{job_id}/{index}.png` instead of a MinIO presigned URL.
- New `GET /v1/images/{job_id}/{index}.png` endpoint streams the image through our service with `require_auth` (either scope).
- `S3_PUBLIC_ENDPOINT` and `PRESIGN_TTL_S` removed from the active config surface (kept in `.env.example` as commented-out for migration; removed entirely in Cycle 10 after downstream verification).
- New env `IMAGE_GEN_PUBLIC_BASE_URL` (dev default `http://127.0.0.1:8700`, prod set to real ingress).

**Why:**
- **Unified auth.** Bearer keys gate both the POST (create) and the GET (fetch) — one credential surface, not one for API + one for presign URLs.
- **Observability.** Cycle 4's orphan reaper sees every image fetch directly (request log) rather than inferring from S3 bucket access logs.
- **Simpler code.** One boto3 client, one config path, no SigV4 `Host` header shenanigans.

**Trade-off:** bandwidth amplification — every image byte flows through the uvicorn process. At LoreWeave's expected scale (≤ 100 concurrent requests × a few MB per image) this is comfortable headroom; if it becomes a bottleneck post-v1, we can re-introduce presigned URLs behind a feature flag.

**Schema / config:**
- `.env.example`: `IMAGE_GEN_PUBLIC_BASE_URL=http://127.0.0.1:8700`; `S3_PUBLIC_ENDPOINT` + `PRESIGN_TTL_S` deprecated.
- Error codes: no new codes; `not_found` (existing §13) covers unknown/unfetched/out-of-range on GET.
- §11 security: image-fetch auth posture documented alongside POST auth.

### v0.5 (2026-04-19) — Cycle 2 amendments (model roster + models/ mount)

Two small but contract-affecting changes landed alongside Cycle 2 BUILD.

**Model roster:** `NoobAI-XL Vpred-1.0` → `NoobAI-XL v1.1` (eps prediction). NoobAI team's current stable; Vpred-1.0 was an experimental branch with unstable training. eps works out-of-the-box with standard SDXL sampler/scheduler defaults, which matters for a service LoreWeave calls without tuning. Fewer moving parts: no `ModelSamplingDiscrete` injection in the workflow, no vpred-specific `prediction` field handling. The vpred injection algorithm in §9 stays documented but is **deferred** — re-introduce only if a future model brings back v-prediction. Workflow filename changes to `workflows/sdxl_eps.json`; `config/models.yaml` example updated.

**`./models/` mount:** `./models:/workspace/ComfyUI/models/checkpoints:ro` → `./models:/workspace/ComfyUI/models:ro` (full ComfyUI models tree under one host directory). ComfyUI expects standard subdirs (`checkpoints/`, `vae/`, `loras/`, etc.) under `models/`; mounting the checkpoints-only subpath prevented external VAE files from resolving. Resulting host layout: `./models/checkpoints/<ckpt>.safetensors`, `./models/vae/<vae>.safetensors`.

**Cycle 5 layout decision deferred:** ComfyUI's `./loras:/workspace/ComfyUI/models/loras:ro` mount was removed from the `comfyui` service in Cycle 2 (no LoRA consumer yet). When Cycle 5 lands, it must pick one of two paths: (A) move LoRAs to `./models/loras/` under the unified tree (requires renaming `/loras` to `/models/loras` on the `image-gen-service` writable mount too), or (B) add `./loras:/workspace/ComfyUI/models/loras:ro` back on the comfyui service alongside the `./models` mount. Option A is cleaner; Option B preserves v0.4's topology. To be resolved in Cycle 5 CLARIFY.

### v0.4 (2026-04-18) — webhook hardening (`/review-impl` response)

An adversarial `/review-impl` pass on v0.3's three safety-sensitive webhook surfaces surfaced 14 findings (4 HIGH, 7 MED, 3 LOW). All resolved in v0.4.

**HIGH fixes:**

- **HIGH-1 DNS rebinding** (§4.8 Security #3) — dispatcher now resolves host once, validates IP, connects by IP with explicit `Host:` header. httpx uses a custom transport to guarantee no late hostname lookup.
- **HIGH-2 Private-IP check on webhook URL** (§4.8 Security #3, §11) — symmetric to the `COMFYUI_URL` startup assertion: resolved IP must be public (non-RFC1918/loopback/link-local/ULA). Dev escape: `WEBHOOK_ALLOW_PRIVATE=true`, refused in prod.
- **HIGH-3 Redirect handling** (§4.8 Security #4) — `follow_redirects=False`; 3xx = `error_code=webhook_redirect` terminal failure. Explicit, separate from Civitai's redirect-following pattern.
- **HIGH-4 Double-delivery on restart** (§4.2, §10) — at-least-once semantics now prominent in §4.2 with named failure modes; §10 receiver contract mandates durable dedupe by `X-ImageGen-Job-Id` and names the two scenarios (sync flush race, retry after lost ack).

**MED fixes:**

- **MED-5 Content-Type + verify-before-parse** — `Content-Type` added to reserved-header set (§6.0); §10 receiver recipe explicit on "verify over raw body bytes BEFORE JSON parse".
- **MED-6 Go receiver recipe** (§10) — literal reference implementation with `hmac.Equal`, lowercase-hex, timestamp parsing, secret-set iteration.
- **MED-7 Timestamp anti-replay** (§4.8, §10) — signing input changed from `body` to `ts + "." + body`; header format changed from `sha256=<hex>` to `t=<ts>,v1=<hex>`; receivers reject `|now - ts| > 300s` (`WEBHOOK_TS_SKEW_S`).
- **MED-8 Allowlist TOCTOU** (§4.8) — every retry attempt re-validates host + IP + signing-secret availability; policy tightening via `POST /admin/reload` now takes effect on the next attempt, not after the 1 h 21 m window.
- **MED-9 Sync 500-after-completion desync** (§4.8 barrier rules) — if sync path fails internally after `status=completed`, job is downgraded to `status=failed, error_code=storage_error` before the response flush, keeping HTTP response and webhook payload consistent.
- **MED-10 Async-no-client-saw-202** (§6.2) — documented as expected at-least-once behavior; receivers must tolerate unknown job ids.
- **MED-11 Dispatcher-before-sync-flush race** (§4.2, §4.8 barrier rules, §12) — new `webhook_handover` SQLite boolean; dispatcher never touches a sync-mode job until the sync handler writes the barrier. Restart-safe (boot recovery sets the barrier so stuck-mid-crash jobs resume).

**LOW fixes:**

- **LOW-12 Multi-secret rotation** (§4.8, §11) — `WEBHOOK_SIGNING_SECRET` (singular, v0.3) replaced by `WEBHOOK_SIGNING_SECRETS` (comma-separated set). Sender signs with first entry; receivers accept any. Rotation SOP documented.
- **LOW-13 Fail-closed allowlist** (§4.8, §11, §16) — `WEBHOOK_ALLOWED_HOSTS` unset is now **deny-all**. Dev permissive requires explicit `WEBHOOK_ALLOW_ANY_HOST=true`, which fails startup in prod.
- **LOW-14 Explicit env mode switch** (§11, §16) — new `IMAGEGEN_ENV={prod,dev}` env var; any other value refuses boot. Gates scheme requirements, allowlist semantics, dev escape flags, startup assertions.

**Schema / config additions:**

- Jobs table: `initial_response_delivered`, `webhook_handover` columns.
- Webhook deliveries table: `error_code` column.
- Env vars: `IMAGEGEN_ENV`, `WEBHOOK_SIGNING_SECRETS` (renamed plural), `WEBHOOK_ALLOW_ANY_HOST`, `WEBHOOK_ALLOW_PRIVATE`, `WEBHOOK_TS_SKEW_S`.
- Error codes: `webhook_ssrf_blocked`, `webhook_redirect` (in addition to v0.3's `webhook_delivery_failed`, `webhook_signing_error`).

### v0.3 (2026-04-18) — webhook delivery

**Added:**
- **§4.8 (new) — Webhook dispatcher** component: separate asyncio task, HMAC-SHA256 signing, 5-attempt jittered retry (15 s → 1 m → 5 m → 15 m → 1 h), `webhook_deliveries` SQLite table with restart recovery, per-delivery uuid4 + stable job_id for receiver dedupe.
- **§4.2 — jobs table** gains `response_delivered`, `webhook_url`, `webhook_headers_json`, `webhook_delivery_status` columns.
- **§6.0 — validation** for `webhook.url` (scheme, allowlist, length) and `webhook.headers` (count, key/value bounds, reserved header protection).
- **§6.2 — async request** schema updated to include optional `webhook` object; `sync + webhook` semantics documented as disconnect-fallback hedge against caller HTTP timeouts (e.g. LoreWeave giving up on a 90 s Chroma job).
- **§6.3 — poll response** surfaces `webhook_delivery_status`.
- **§6.9 (new) — `GET /v1/webhooks/deliveries/{job_id}`** admin endpoint for debugging delivery failures.
- **§7 — lifecycle diagram** extended with webhook dispatcher branch.
- **§10 — integration guide amendments** expanded: webhook receiver guidance for LoreWeave (HMAC verify recipe, dedupe by job id, 10 s response SLA); suggested `POST /v1/webhooks/image-gen` receiver route. Ordering constraint split: webhook can ship independently of async if LoreWeave exposes a receiver.
- **§11 — security**: webhook signing secret separate-blast-radius rationale; SSRF prevention via `WEBHOOK_ALLOWED_HOSTS`; URL logging with signature redaction.
- **§12 — concurrency**: dispatcher bounded by `WEBHOOK_MAX_IN_FLIGHT` (default 8); per-attempt HTTP timeout `WEBHOOK_HTTP_TIMEOUT_S` (default 10 s); graceful-shutdown drain for in-flight deliveries.
- **§13 — error codes**: `webhook_delivery_failed`, `webhook_signing_error`.
- **§13 — audit log**: webhook attempt events (job_id, url, attempt_n, status_code, duration_ms, outcome; signature never logged).
- **§15 — repo layout**: new `app/webhooks/` package (`dispatcher.py`, `signing.py`, `retry.py`).
- **§16 — startup checks**: webhook-allowlist DNS sanity + signing-secret presence warning.

**Four decisions captured from review:**
1. Global env signing secret (not per-request named secret).
2. Retry schedule: 5 attempts / ~1h 21m total (15 s → 1 m → 5 m → 15 m → 1 h).
3. Terminal events only (`completed`, `failed`, `abandoned`) — no `running`/progress webhooks in v1.
4. `WEBHOOK_ALLOWED_HOSTS` opt-in strict allowlist; unset = permissive (dev).

### v0.2 (2026-04-18) — four-perspective review response

**Convergent HIGHs resolved:**

- **[HIGH-A] State loss on restart** — §4.2 now uses SQLite (`./data/jobs.db`) with boot reconciliation; restart marks running→failed with `error_code=service_restarted`, queued re-enqueued.
- **[HIGH-B] Sync-disconnect → orphan blob** — §4.2 + §7 add `asyncio.shield` + disconnect listener (flips mode to async) + orphan reaper + MinIO lifecycle.
- **[HIGH-C] ComfyUI workflow coupling** — §9 introduces anchor-node convention (`_meta.title = "%...%"`) so the adapter touches named roles, not hardcoded ids. §4.7 pins ComfyUI and all custom nodes.
- **[HIGH-D] ComfyUI prompt-API contract** — §4.3 spells out `client_id`, WebSocket completion signal, `/history` fallback, `/view` anchor-based lookup.
- **[HIGH-E] Chroma GGUF custom nodes** — §4.7 new section; `config/models.yaml` now lists `clip_l`, `t5xxl`, `dual_clip_type`.
- **[HIGH-F] Civitai threat surface** — §4.5 expanded to 11 hardening rules (host allowlist, SHA-256, version_id required, per-URL lock, admin scope, total-dir ceiling, extension allowlist, …).
- **[HIGH-G] Prod network exposure** — §5 uses private network, no published ports in prod; §11 adds startup assertion refusing to boot if dependents resolve to public IPs.

**Unique HIGHs resolved:**

- **LoRA path traversal** — §6.0 + §9 enforce name regex and realpath containment.
- **Single API key / rotation** — §11 introduces a multi-key set with `kid` logging and a two-scope split (`API_KEYS` vs `ADMIN_API_KEYS`).
- **ComfyUI cancel mechanics** — §4.3 + §12 correct the mechanism (`/interrupt` + `DELETE /queue` + `/free`), and no longer promise immediate VRAM release on interrupt.

**MEDs resolved:**

- Input validation table (§6.0) caps `n`, `steps`, `size`, `cfg`, prompt length, LoRA count.
- Async feature-flagged off by default (§6.2) until integration-guide addendum lands (§10 ordering).
- S3 two-endpoint model (§4.6): separate internal + public boto3 clients, no host-string-swap.
- Sampler + scheduler split, `prediction: vpred|eps` added, `ModelSamplingDiscrete` injection documented (§4.4, §9).
- Civitai API fields named: `files[].primary`, `downloadUrl`, redirect-following, `CIVITAI_API_TOKEN` (§4.5).
- Startup validation (§16) fails fast on missing models/workflows/anchors.
- Error code enum (§13).
- MinIO upload retry with tenacity (§4.6).
- VRAM guard at router (§12).
- Audit log stream (§13).
- `./loras` mounted `:ro` for ComfyUI (§5, §11).

**LOWs resolved:**

- Replaced SIGHUP with `POST /admin/reload` + watchfiles fallback (Windows-safe) (§6.8).
- Presigned URL redaction explicit (§4.6, §13).
- `/health` returns 503 on dependent down; auth-gated verbose shape (§6.7).
- Dependency pins added (§14).

### v0.1 (2026-04-18) — initial draft

- First architecture pass; captured 7 user decisions (Docker + Novita, 12 GB VRAM, ComfyUI sidecar, MinIO, sync+async, Civitai+local LoRAs, 1-worker queue).

---

*End of architecture v0.2.*
