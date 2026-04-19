from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

# ───────────────────────── test-wide env ─────────────────────────
# Required by lifespan. Values deliberately weak — these are test-only.
os.environ.setdefault("API_KEYS", "test-gen-key,test-gen-key-2")
os.environ.setdefault("ADMIN_API_KEYS", "test-admin-key")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("LOG_PROMPTS", "false")
os.environ.setdefault("IMAGEGEN_ENV", "dev")
# S3 — ensure_bucket is monkeypatched in the `client` fixture so these don't hit a network.
os.environ.setdefault("S3_INTERNAL_ENDPOINT", "http://test-s3:9000")
os.environ.setdefault("S3_BUCKET", "image-gen-test")
os.environ.setdefault("S3_ACCESS_KEY", "test-s3-key")
os.environ.setdefault("S3_SECRET_KEY", "test-s3-secret")
# Registry paths — point at the real committed files on disk.
os.environ.setdefault("MODELS_YAML_PATH", "config/models.yaml")
os.environ.setdefault("MODELS_ROOT", str(Path(__file__).parent.parent / "models"))
os.environ.setdefault("WORKFLOWS_ROOT", str(Path(__file__).parent.parent))
os.environ.setdefault("VRAM_BUDGET_GB", "12")
# Gateway URL for response URLs in tests.
os.environ.setdefault("IMAGE_GEN_PUBLIC_BASE_URL", "http://testserver")
# ComfyUI — test env uses a placeholder; adapter is swapped out by sync-endpoint tests.
os.environ.setdefault("COMFYUI_URL", "http://test-comfy:8188")
os.environ.setdefault("COMFYUI_WS_URL", "ws://test-comfy:8188/ws")


async def _noop_ensure_bucket(self) -> None:
    return None


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """App instance with a fresh SQLite file + S3.ensure_bucket patched to no-op."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jobs.db"))
    # Patch ensure_bucket BEFORE app.main is imported so the lifespan sees the no-op.
    monkeypatch.setattr("app.storage.s3.S3Storage.ensure_bucket", _noop_ensure_bucket)

    from app.main import app

    async with LifespanManager(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


@pytest.fixture
async def client_with_loras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """App wired to an isolated, seeded `./loras/` directory.

    Layout:
      <tmp>/loras/style_alpha.safetensors      (addressable, no sidecar)
      <tmp>/loras/style_alpha.json              (sidecar with metadata)
      <tmp>/loras/hanfu/Bai_LingMiao.safetensors (addressable, subdir)
      <tmp>/loras/bad name (1).safetensors     (unaddressable — space+parens)
    """
    loras_root = tmp_path / "loras"
    (loras_root / "hanfu").mkdir(parents=True)
    (loras_root / "style_alpha.safetensors").write_bytes(b"\x00" * 16)
    (loras_root / "style_alpha.json").write_text(
        '{"sha256":"abc","source":"civitai","trigger_words":["anime"]}',
        encoding="utf-8",
    )
    (loras_root / "hanfu" / "Bai_LingMiao.safetensors").write_bytes(b"\x00" * 32)
    (loras_root / "bad name (1).safetensors").write_bytes(b"\x00" * 8)
    monkeypatch.setenv("LORAS_ROOT", str(loras_root))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr("app.storage.s3.S3Storage.ensure_bucket", _noop_ensure_bucket)

    from app.main import app

    async with LifespanManager(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


@pytest.fixture
async def broken_db_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """App wired to a DB that gets closed mid-test so healthcheck fails."""
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.storage.s3.S3Storage.ensure_bucket", _noop_ensure_bucket)

    from app.main import app

    async with LifespanManager(app):
        await app.state.store.close()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c
