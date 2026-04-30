# local-image-generator-service

Self-hostable, OpenAI-compatible image-generation microservice that integrates with LoreWeave's provider-registry. Wraps one or more image-generation backends (ComfyUI first) behind a unified API; supports uncensored community models (NoobAI-XL, Chroma1-HD, Illustrious merges) without per-model server code.

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
scripts/       workflow enforcement + dev helpers
workflows/     ComfyUI workflow templates (Cycle 2+)
config/        models.yaml registry (Cycle 3+)
```

## Development workflow

This repo uses a 12-phase agentic workflow with state-machine enforcement. See [CLAUDE.md](CLAUDE.md) for the full rules and [scripts/workflow-gate.sh](scripts/workflow-gate.sh) for the gate script.

## Current status

**Sprint 3 / Cycle 0** — repo bootstrap. Service boots, `/health` returns 200, Compose topology (three services on private network) validated. Next: Cycle 1 (auth + SQLite + structured logging).

## License

MIT — see [LICENSE](LICENSE).
