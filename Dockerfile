# syntax=docker/dockerfile:1.7

# ── Stage 1: builder ─────────────────────────────────────────────────────────
# Install deps into a standalone venv using uv. The runtime stage won't ship
# uv itself — saves ~30 MB of dead weight in the final image.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# `--no-install-project` skips building our local package (we import app/ from
# WORKDIR at runtime — no hatchling/README round trip needed).
COPY pyproject.toml .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Copy the pre-built virtualenv from the builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy application code + entrypoint + migrations (SQLite schema applied on lifespan).
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Non-root user
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Container-level healthcheck — orchestrators (Swarm / k8s) inherit this if
# compose doesn't override. `urllib.request` is stdlib so no extra binary needed.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
