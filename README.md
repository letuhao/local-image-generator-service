# local-image-generator-service

Self-hostable, OpenAI-compatible image-generation microservice that integrates with LoreWeave's provider-registry. Wraps one or more image-generation backends (ComfyUI first) behind a unified API; supports uncensored community models (NoobAI-XL, Chroma1-HD, Illustrious merges) without per-model server code.

Batch tooling drives large sprite/tile matrices against the same HTTP API: **HoMM3-inspired biome bundles** (terrain through mushroom lanes), **tree-environment** Flux sprites, and **`scripts/run-homm3-tree-bundle.py`** to run everything under one folder tree. Use **`--resume`** on batch scripts to skip generations whose PNG already exists after an interrupted run.

## Architecture

- **Spec:** [docs/architecture/image-gen-service.md](docs/architecture/image-gen-service.md) (v0.4)
- **Integration contract:** [docs/EXTERNAL_AI_SERVICE_INTEGRATION_GUIDE.md](docs/EXTERNAL_AI_SERVICE_INTEGRATION_GUIDE.md)
- **Implementation plan:** [docs/plans/2026-04-18-image-gen-service-build.md](docs/plans/2026-04-18-image-gen-service-build.md) — 11 cycles
- **Session log:** [docs/session/SESSION.md](docs/session/SESSION.md)

## Quickstart (dev)

Prerequisites: Docker Desktop with NVIDIA Container Toolkit (required from Cycle 2 onward), [uv](https://github.com/astral-sh/uv).

```bash
cp .env.example .env
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose build
docker compose up -d
curl http://127.0.0.1:8700/health
# → {"status":"ok"}
```

Fill `API_KEYS` (and optionally `ADMIN_API_KEYS`) in `.env` before calling authenticated routes.
For faster local startup, set `STARTUP_SMOKE_ENABLED=false` in `.env` to skip model smoke
generations at boot (recommended when using runtime reload/setup endpoints during development).
If sync requests can block each other under long-running jobs, raise `WORKER_CONCURRENCY`
above `1` in `.env` so multiple queue consumers can process jobs in parallel.

### MinIO web UI and host ports (dev)

With the example override, MinIO is reachable on the loopback interface:

- **Console:** `http://127.0.0.1:9101` — sign in with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` from `.env`, open bucket **`image-gen`**, browse **`generations/`** to see uploaded PNGs.
- **S3 API on host:** `http://127.0.0.1:9100` (for `aws s3`, MinIO Client, etc.).

If ports **9100** / **9101** are already taken, set `MINIO_HOST_PORT` and `MINIO_CONSOLE_HOST_PORT` in `.env`, then `docker compose up -d` again. Use `MINIO_BIND=0.0.0.0` only if you intentionally want LAN exposure (not recommended on untrusted networks).

### Higher-quality smoke request (optional)

After `API_KEYS` is set, generate a fuller SDXL image and open the returned URL in a browser (same Bearer token):

```bash
curl -sS -X POST http://127.0.0.1:8700/v1/images/generations \
  -H "Authorization: Bearer REPLACE_WITH_FIRST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"noobai-xl-v1.1","prompt":"cinematic landscape at golden hour, volumetric light, detailed","size":"1024x1024","steps":28,"cfg":5.0,"seed":42}' | jq .
```

### Runtime bundle operations (no compose restart)

Admin endpoints allow model-registry reload and trial bundle lifecycle changes without
restarting containers.

```bash
# Reload config/models.yaml into the live registry.
curl -sS -X POST http://127.0.0.1:8700/v1/admin/runtime/reload-registry \
  -H "Authorization: Bearer REPLACE_WITH_ADMIN_API_KEY" | jq .

# Activate a trial bundle (optional warmup smoke per model).
curl -sS -X POST http://127.0.0.1:8700/v1/admin/runtime/setup-bundle \
  -H "Authorization: Bearer REPLACE_WITH_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model_names":["noobai-xl-v1.1","terrain-sdxl-base"],"warmup":false}' | jq .

# Close bundle and request backend unload/free.
curl -sS -X POST http://127.0.0.1:8700/v1/admin/runtime/close-bundle \
  -H "Authorization: Bearer REPLACE_WITH_ADMIN_API_KEY" | jq .
```

While runtime reconfiguration is in progress, generation requests may return:

- `503` / `runtime_reconfiguring`

### Monitoring endpoints

The service now exposes two monitoring surfaces with split visibility:

- Public safe Prometheus scrape: `GET /metrics` (no auth)
- Detailed runtime/admin JSON:
  - `GET /v1/admin/monitoring/status`
  - `GET /v1/admin/monitoring/queue`
  - both require `ADMIN_API_KEYS`

```bash
# Prometheus scrape target (safe/public fields only).
curl -sS http://127.0.0.1:8700/metrics

# Admin runtime snapshot (sensitive runtime internals).
curl -sS http://127.0.0.1:8700/v1/admin/monitoring/status \
  -H "Authorization: Bearer REPLACE_WITH_ADMIN_API_KEY" | jq .

# Admin queue and worker summary.
curl -sS http://127.0.0.1:8700/v1/admin/monitoring/queue \
  -H "Authorization: Bearer REPLACE_WITH_ADMIN_API_KEY" | jq .
```

### Model + preset discovery endpoints

For user/LLM clients that need confirmed combo metadata and richer model details:

```bash
# OpenAI-compatible model list (now includes extra metadata fields).
curl -sS http://127.0.0.1:8700/v1/models \
  -H "Authorization: Bearer REPLACE_WITH_FIRST_API_KEY" | jq .

# Rich catalog: per-model defaults/limits + confirmed presets.
curl -sS http://127.0.0.1:8700/v1/catalog/models \
  -H "Authorization: Bearer REPLACE_WITH_FIRST_API_KEY" | jq .

# Preset catalog (includes confirmed flag).
curl -sS http://127.0.0.1:8700/v1/catalog/presets \
  -H "Authorization: Bearer REPLACE_WITH_FIRST_API_KEY" | jq .

# Preset detail by id.
curl -sS http://127.0.0.1:8700/v1/catalog/presets/terrain-53858-v1 \
  -H "Authorization: Bearer REPLACE_WITH_FIRST_API_KEY" | jq .
```

Flux-family models now appear in discovery endpoints with `family: "flux"` while
using the same universal generation payload shape as SDXL models.

### Flux Dev GGUF (Q8) — workflow and sampler defaults

Flux GGUF models (`flux1-dev-q8`, `flux1-dev-q8-tree`, …) use **`workflows/flux_gguf.json`**
(`UnetLoaderGGUF` + `DualCLIPLoader` + external `VAELoader`), not the legacy chroma-named template.

For this stack, treat **`cfg: 1.0`**, **`sampler: euler`**, **`scheduler: simple`** as the baseline.
Higher CFG or SDXL-style samplers (e.g. DPM++ + Karras) tend to over-smooth or drift quality.

**Steps:** more steps mainly improve **denoising convergence** (structure/texture coherence), not
intrinsic pixel sharpness. In practice, **24** is a reasonable default; **32–36** can help
hero assets; beyond that you usually see diminishing returns and longer GPU time.

**LoRAs:** request payloads accept `loras: [{ "name": "…", "weight": … }]`.  
The `name` is a **path under `models/loras/`** on the host (POSIX, **no** `.safetensors` suffix), e.g. `flux/FluxMythV2`. Compose mounts **`./models/loras` → `/app/loras`** for the API (writable for Civitai fetch + sidecars) and ComfyUI reads the **same files** via `./models` → `/workspace/ComfyUI/models` — no separate top-level `./loras/` mount anymore.

If you upgraded from an older compose file that used `./loras/`, move hand-placed files into `models/loras/` (and move `loras/civitai/` → `models/loras/civitai/` if you relied on fetched LoRAs).

Example (Flux tree preset + style LoRA — **operator default:** `flux/dark_fantasy_digital_v11`; see [flux-lora-tree-batch-results.md](docs/architecture/flux-lora-tree-batch-results.md)):

```bash
curl -sS -X POST http://127.0.0.1:8700/v1/images/generations/binary \
  -H "Authorization: Bearer REPLACE_WITH_FIRST_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"flux1-dev-q8-tree\",\"prompt\":\"sprite object, ancient oak tree, top-down degree view, shallow root, white background, white ground, HoMM3 art style\",\"size\":\"1024x1024\",\"transparent_background\":true,\"steps\":28,\"cfg\":1.0,\"sampler\":\"euler\",\"scheduler\":\"simple\",\"seed\":404,\"loras\":[{\"name\":\"flux/dark_fantasy_digital_v11\",\"weight\":0.8}]}" \
  --output tree_flux_lora.png
```

### Objective image metrics (batch / A-B)

For repeatable comparisons (sharpness proxies, entropy, alpha coverage), use:

```bash
uv run python scripts/image-quality-metrics.py "outputs/tree-review/**/*.png" \
  --csv-out outputs/tree-review/image-quality-metrics.csv
```

Scores are best interpreted **relative to your own baseline folder**, not as universal absolutes.

### Broad asset review batch (Flux lane)

Use the generic matrix runner for terrain/object/POI/decor smoke passes:

```bash
python scripts/asset-review-batch.py --api-key REPLACE_WITH_FIRST_API_KEY \
  --pack docs/architecture/flux-asset-review-pack.json \
  --out-dir outputs/asset-review/pass-001
```

### Tree environment batch

Default pack **`docs/architecture/tree-environment-batch-pack.json`** targets **Flux GGUF** (`flux1-dev-q8-tree`): **biome-specific `tree_species`** (no tropical trees in glacier / infernal variants for lava-volcano lanes), **three seeds per species** as variants, **2.5D orthographic sprite** wording in `prompt_template`, optional **`sampler` / `scheduler`**, and default LoRA **`flux/dark_fantasy_digital_v11`** (see [flux-lora-tree-batch-results.md](docs/architecture/flux-lora-tree-batch-results.md)).

Legacy SDXL fantasy lane (single global `tree_types` × all environments):

```bash
python scripts/tree-environment-batch.py --api-key REPLACE_WITH_FIRST_API_KEY \
  --pack docs/architecture/tree-environment-batch-pack-sdxl.json \
  --out-dir outputs/tree-review/pass-sdxl-001
```

Flux lane (uses pack model / LoRA / species maps):

```bash
python scripts/tree-environment-batch.py --api-key REPLACE_WITH_FIRST_API_KEY \
  --pack docs/architecture/tree-environment-batch-pack.json \
  --out-dir outputs/tree-review/pass-flux-001
```

Dry-run plan only:

```bash
python scripts/tree-environment-batch.py --api-key REPLACE_WITH_FIRST_API_KEY \
  --pack docs/architecture/tree-environment-batch-pack.json --dry-run
```

**Resume:** append **`--resume`** to skip calls when the target PNG already exists (non-empty file).

The **`run-homm3-tree-bundle`** orchestrator can omit **`--api-key`** when **`API_KEYS`** is set in your shell (comma-separated; **first** key is used — same variable name as Compose). **`tree-environment-batch.py`** invoked directly still requires **`--api-key`** (pass your key or a placeholder with **`--dry-run`**).

### HoMM3-inspired biome bundle (terrain + structures + flora)

Matrix batches expand **biomes × entries × sizes × seeds** with Flux parity (`loras`, `sampler`, `scheduler`). Filenames embed **`WxH`** (e.g. `mine_gold_entrance__1024x1024__s101.png`). Optional per-entry **`sizes`** override the pack default (e.g. **`1024x1536`** tall sprites, **`1536x1024`** wide compositions within model **`size_max_pixels`**). Optional authoring metadata on entries is copied into **`*.prompt.json`** / **`asset-manifest.ndjson`**: **`footprint_tiles_w`**, **`footprint_tiles_h`**, **`composition`** (`single_tile` \| `tall_sprite` \| `wide_scene` \| `dense_tile`), **`category`**.

Shared biome **`id`** keys across packs align with **`tree-environment-batch-pack.json`** `environments[].id`: **fifteen** regions total — the earlier nine (including **`coastal_water`**) plus **`spectral_ethereal`**, **`necropolis_blight`**, **`ocean_abyssal`**, **`drake_badlands`**, **`heaven_cloud`** (celestial cloud deck tiles / props), and **`abyss_chaos_rift`** (underground chaos cavern read distinct from **`ocean_abyssal`**). The terrain pack adds dedicated **`biomes_include`** rows for heaven and chaos cave floor/path tiles (see **`cloud_marble_mosaic_floor`**, **`chaos_rift_fractured_basalt_floor`**, etc.).

Outputs under `--out-dir`: `<biome>/terrain/`, `<biome>/structures/`, `<biome>/misc/`, `<biome>/bush/`, or `<biome>/mushroom/`, plus sidecars and **`asset-manifest.ndjson`**.

#### Full bundle orchestrator (HoMM lanes + trees)

One command walks all five HoMM packs **then** the tree batch. HoMM lanes write under **`<bundle-root>/homm3/<lane>/`** so each lane keeps its own **`batch-run-log.json`** and **`asset-manifest.ndjson`**; trees write to **`<bundle-root>/trees/`**. With default packs this schedules on the order of **~2 445** PNG generations (~2 310 HoMM + ~135 trees); use **`--resume`** after stopping mid-run.

```bash
python scripts/run-homm3-tree-bundle.py \
  --bundle-root outputs/homm3-bundle/pass-full-001 \
  --base-url http://127.0.0.1:8700 \
  --api-key REPLACE_WITH_FIRST_API_KEY

# Same, but infer API key from the first entry in env API_KEYS:
python scripts/run-homm3-tree-bundle.py \
  --bundle-root outputs/homm3-bundle/pass-full-001 \
  --base-url http://127.0.0.1:8700

# Continue after Ctrl+C / disconnect (skip existing PNGs):
python scripts/run-homm3-tree-bundle.py \
  --bundle-root outputs/homm3-bundle/pass-full-001 \
  --base-url http://127.0.0.1:8700 \
  --resume
```

Orchestrator summary: **`<bundle-root>/bundle-orchestrator-summary.json`**. **`--skip-homm3`** / **`--skip-trees`** run only half the bundle.

Packs:

- **`docs/architecture/homm3-flux-terrain-biome-pack.json`** — terrain + dense variants + vertical strata (**`sizes`** on selected entries).
- **`docs/architecture/homm3-flux-structure-biome-pack.json`** — structures + tall/wide landmark fragments (**`coastal_water`**; ship wreck uses **`biomes_include`**).
- **`docs/architecture/homm3-flux-misc-biome-pack.json`** — small clutter props (**`misc_entries`** lane).
- **`docs/architecture/homm3-flux-bush-biome-pack.json`** — shrub sprites (**`bush_entries`** lane).
- **`docs/architecture/homm3-flux-mushroom-biome-pack.json`** — fungal clusters (**`mushroom_entries`** lane).

```bash
python scripts/homm3-biome-bundle-batch.py --pack docs/architecture/homm3-flux-terrain-biome-pack.json \
  --out-dir outputs/homm3-bundle/pass-terrain-001 --dry-run

python scripts/homm3-biome-bundle-batch.py --pack docs/architecture/homm3-flux-terrain-biome-pack.json \
  --api-key REPLACE_WITH_FIRST_API_KEY --out-dir outputs/homm3-bundle/pass-terrain-001

python scripts/homm3-biome-bundle-batch.py --pack docs/architecture/homm3-flux-structure-biome-pack.json \
  --api-key REPLACE_WITH_FIRST_API_KEY --out-dir outputs/homm3-bundle/pass-structures-001

python scripts/homm3-biome-bundle-batch.py --pack docs/architecture/homm3-flux-misc-biome-pack.json \
  --api-key REPLACE_WITH_FIRST_API_KEY --out-dir outputs/homm3-bundle/pass-misc-001

python scripts/homm3-biome-bundle-batch.py --pack docs/architecture/homm3-flux-bush-biome-pack.json \
  --api-key REPLACE_WITH_FIRST_API_KEY --out-dir outputs/homm3-bundle/pass-bush-001

python scripts/homm3-biome-bundle-batch.py --pack docs/architecture/homm3-flux-mushroom-biome-pack.json \
  --api-key REPLACE_WITH_FIRST_API_KEY --out-dir outputs/homm3-bundle/pass-mushroom-001
```

**Resume:** **`--resume`** skips scenarios whose output PNG already exists (non-empty) and **does not delete** **`asset-manifest.ndjson`** at lane start so partial manifests remain.

### Transparent background option

All generation payloads now accept `transparent_background` (default `true`).
When enabled, near-white backgrounds are converted to transparent alpha in output PNGs.

```bash
curl -sS -X POST http://127.0.0.1:8700/v1/images/generations/binary \
  -H "Authorization: Bearer REPLACE_WITH_FIRST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"terrain-realisticfantasy-v30","prompt":"isometric tree sprite","transparent_background":true}' \
  --output sprite.png
```

## Run tests locally

```bash
uv sync
uv run pytest -q
uv run ruff check .
```

Integration tests (require running Compose stack) are gated by the `integration` pytest marker:

```bash
uv run pytest -m integration -q
```

## Project structure

```
app/           FastAPI application
tests/         pytest suites
docker/        Dockerfiles for sidecar containers (Cycle 2+)
docs/          architecture, plans, session log, integration contract
scripts/       workflow enforcement, HoMM/tree batch runners, orchestrator (`run-homm3-tree-bundle.py`)
workflows/     ComfyUI workflow templates (Cycle 2+)
config/        models.yaml registry (Cycle 3+)
```

## Development workflow

This repo uses a 12-phase agentic workflow with state-machine enforcement. See [CLAUDE.md](CLAUDE.md) for the full rules and [scripts/workflow-gate.sh](scripts/workflow-gate.sh) for the gate script.

## Current status

**Under active development** — FastAPI generation surface over ComfyUI, SQLite job store, optional MinIO artifact storage, Civitai LoRA helpers, Flux GGUF workflows, monitoring endpoints, and CLI batch pipelines (HoMM biome bundles, tree sprites, orchestrator with **`--resume`**). Treat production rollout as gated on your own SLAs, secrets hygiene, GPU capacity, and model licensing — see architecture docs for operational detail.

## License

MIT — see [LICENSE](LICENSE).
