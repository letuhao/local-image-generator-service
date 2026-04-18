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
