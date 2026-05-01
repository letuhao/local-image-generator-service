# syntax=docker/dockerfile:1.7

# ── Stage 1: builder ─────────────────────────────────────────────────────────
# Install deps into a standalone venv using uv. The runtime stage won't ship
# uv itself — saves ~30 MB of dead weight in the final image.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/root/.cache/uv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependency layer: copy lockfile so Docker invalidates this stage only when deps change,
# and use `--frozen` so `uv` does not re-resolve (fast + reproducible).
# BuildKit cache mount persists downloaded wheels (torch/CUDA/etc.) across rebuilds —
# requires BuildKit (`DOCKER_BUILDKIT=1`, default on recent Docker Desktop).
#
# Persistent remote/local cache for CI or cold machines:
#   docker buildx build --cache-to type=registry,ref=your/repo:uv-cache,mode=max \
#     --cache-from type=registry,ref=your/repo:uv-cache .
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Non-root user must exist before COPY --chown. Avoid `chown -R /app/.venv`:
# torch/CUDA trees have massive file counts and look “stuck forever” on Docker Desktop (Windows).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Copy the pre-built virtualenv + app as appuser (no recursive chown pass).
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser migrations/ ./migrations/
COPY --chown=appuser:appuser docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER appuser

EXPOSE 8000

# Container-level healthcheck — orchestrators (Swarm / k8s) inherit this if
# compose doesn't override. `urllib.request` is stdlib so no extra binary needed.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
