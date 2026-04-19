from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from app.loras.civitai import CivitaiFetcher
from app.queue.fetches import create_pending, get_by_id
from app.queue.store import JobStore

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[JobStore]:
    s = JobStore(str(tmp_path / "jobs.db"))
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
def loras_root(tmp_path: Path) -> Path:
    root = tmp_path / "loras"
    root.mkdir()
    return root


def _fetcher(
    store: JobStore,
    http: httpx.AsyncClient,
    loras_root: Path,
    *,
    file_max_bytes: int = 50_000_000,
    dir_max_bytes: int = 100_000_000,
) -> CivitaiFetcher:
    return CivitaiFetcher(
        store=store,
        loras_root=loras_root,
        api_token="test-token",
        http_client=http,
        dir_max_bytes=dir_max_bytes,
        file_max_bytes=file_max_bytes,
        recent_use_days=7,
    )


def _metadata(
    *,
    size_kb: int = 10,
    sha: str = "a" * 64,
    name: str = "test_lora.safetensors",
    download_url: str = "https://civitai.com/cdn/file.safetensors",
    base_model: str = "SDXL 1.0",
    triggers: list[str] | None = None,
) -> dict:
    return {
        "id": 999,
        "modelId": 123,
        "baseModel": base_model,
        "trainedWords": triggers or ["trig1", "trig2"],
        "files": [
            {
                "primary": True,
                "name": name,
                "sizeKB": size_kb,
                "hashes": {"SHA256": sha},
                "downloadUrl": download_url,
            }
        ],
    }


async def _run_and_wait(fetcher: CivitaiFetcher, store: JobStore, request_id: str) -> None:
    fetcher.enqueue(request_id)
    # enqueue returns immediately; wait for the task to complete.
    # Active tasks are tracked on the fetcher; poll.
    import asyncio

    for _ in range(200):
        fetch = await get_by_id(store, request_id)
        if fetch and fetch.status in ("done", "failed"):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("fetch did not terminate within 2s")


# ── Tests ────────────────────────────────────────────────────────────


async def test_happy_path_downloads_and_verifies(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    payload = b"\x00\x01\x02" * 1000  # 3000 bytes
    sha = hashlib.sha256(payload).hexdigest()
    meta = _metadata(size_kb=3, sha=sha)

    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        respx.get("https://civitai.com/cdn/file.safetensors").respond(200, content=payload)

        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(
            store,
            url="https://civitai.com/models/123?modelVersionId=456",
            civitai_model_id=123,
            civitai_version_id=456,
        )
        await _run_and_wait(fetcher, store, row.id)

    final = await get_by_id(store, row.id)
    assert final is not None
    assert final.status == "done"
    assert final.dest_name == "civitai/test_lora_456"
    dest = loras_root / "civitai" / "test_lora_456.safetensors"
    assert dest.is_file()
    assert dest.read_bytes() == payload
    sidecar = json.loads((loras_root / "civitai" / "test_lora_456.json").read_text())
    assert sidecar["sha256"] == sha
    assert sidecar["source"] == "civitai"
    assert sidecar["civitai_version_id"] == 456
    assert sidecar["trigger_words"] == ["trig1", "trig2"]


async def test_metadata_401_fails_civitai_auth(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(401)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)

    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "civitai_auth"


async def test_metadata_404_fails_version_not_found(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/789").respond(404)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=None, civitai_version_id=789)
        await _run_and_wait(fetcher, store, row.id)
    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "civitai_version_not_found"


async def test_metadata_5xx_retries_then_unavailable(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    with respx.mock:
        route = respx.get("https://civitai.com/api/v1/model-versions/999").respond(503)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=None, civitai_version_id=999)
        await _run_and_wait(fetcher, store, row.id)

    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "civitai_unavailable"
    assert route.call_count == 3  # tenacity 3x


async def test_missing_sha256_fails_validation(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    meta = _metadata()
    # Strip SHA256.
    meta["files"][0]["hashes"] = {"AutoV2": "abc"}
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)
    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "validation_error"
    assert "SHA256" in final.error_message


async def test_size_over_cap_fails_too_large(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    meta = _metadata(size_kb=50_000)  # 50 MB > 10 MB cap
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        fetcher = _fetcher(store, http, loras_root, file_max_bytes=10_000_000)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)
    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "lora_too_large"


async def test_sha_mismatch_fails_and_unlinks_partial(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    payload = b"\x00" * 1000
    wrong_sha = "b" * 64  # does NOT match payload
    meta = _metadata(size_kb=1, sha=wrong_sha)
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        respx.get("https://civitai.com/cdn/file.safetensors").respond(200, content=payload)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)
    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "sha_mismatch"
    # No leftover tmp or final file.
    assert not (loras_root / "civitai" / "test_lora_456.safetensors").exists()
    assert not (loras_root / "civitai" / "test_lora_456.safetensors.tmp").exists()


async def test_non_safetensors_name_fails_validation(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    meta = _metadata(name="malicious.exe")
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)
    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "validation_error"
    assert ".safetensors" in final.error_message


async def test_idempotent_existing_file_short_circuits(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    # Pre-place a file at the destination.
    dest = loras_root / "civitai" / "test_lora_456.safetensors"
    dest.parent.mkdir(parents=True)
    preexisting = b"preexisting"
    dest.write_bytes(preexisting)

    meta = _metadata(sha="c" * 64)  # mismatched, but download won't happen
    with respx.mock:
        route_meta = respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        route_dl = respx.get("https://civitai.com/cdn/file.safetensors")
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)

        assert route_meta.called  # metadata still fetched for sidecar
        assert not route_dl.called  # no redownload

    final = await get_by_id(store, row.id)
    assert final.status == "done"
    assert final.dest_name == "civitai/test_lora_456"
    # File untouched.
    assert dest.read_bytes() == preexisting
    # Sidecar was written retroactively.
    assert (loras_root / "civitai" / "test_lora_456.json").is_file()


async def test_empty_files_array_fails_validation(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    meta = {"files": [], "baseModel": "SDXL"}
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)
    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "validation_error"
    assert "files" in final.error_message


# ── /review-impl fixes (Cycle 6) ─────────────────────────────────────


async def test_ssrf_rejected_download_url_http_scheme(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    """MED-1 / LOW-8: http:// downloadUrl refused before streaming."""
    meta = _metadata(download_url="http://minio:9000/evil.safetensors")
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)
    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "validation_error"
    assert "unsafe download URL" in final.error_message
    assert "https" in final.error_message


async def test_ssrf_rejected_download_url_internal_host(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    """MED-1: downloadUrl pointing at an internal/non-Civitai host refused."""
    meta = _metadata(download_url="https://169.254.169.254/latest/meta-data/")
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)
    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "validation_error"
    assert "allowlist" in final.error_message


async def test_ssrf_accepts_cdn_subdomain(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    """MED-1: legitimate CDN hostnames under *.civitai.com are accepted."""
    payload = b"\x00" * 512
    sha = hashlib.sha256(payload).hexdigest()
    meta = _metadata(
        size_kb=1,
        sha=sha,
        download_url="https://civitai-delivery-worker-prod.civitai.com/blob/abc",
    )
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        respx.get("https://civitai-delivery-worker-prod.civitai.com/blob/abc").respond(
            200, content=payload
        )
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)
    final = await get_by_id(store, row.id)
    assert final.status == "done"


async def test_oserror_mid_download_cleans_tmp_and_maps_error(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    """MED-2: OSError during write → tmp unlinked + insufficient_storage on ENOSPC."""
    from unittest.mock import patch

    payload = b"\x00" * 2048
    sha = hashlib.sha256(payload).hexdigest()
    meta = _metadata(size_kb=2, sha=sha)

    real_open = Path.open

    class _FailingFile:
        def __init__(self, real):
            self._real = real

        def __enter__(self):
            self._real.__enter__()
            return self

        def __exit__(self, *a):
            return self._real.__exit__(*a)

        def write(self, _data):
            raise OSError(28, "No space left on device")

        def __getattr__(self, name):
            return getattr(self._real, name)

    def patched_open(self, *args, **kwargs):
        real = real_open(self, *args, **kwargs)
        if str(self).endswith(".safetensors.tmp"):
            return _FailingFile(real)
        return real

    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        respx.get("https://civitai.com/cdn/file.safetensors").respond(200, content=payload)
        with patch.object(Path, "open", patched_open):
            fetcher = _fetcher(store, http, loras_root)
            row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
            await _run_and_wait(fetcher, store, row.id)

    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "insufficient_storage"
    # tmp cleaned up — no leftover.
    tmps = list((loras_root / "civitai").glob("*.tmp"))
    assert tmps == []


async def test_slow_loris_throughput_check_aborts(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    """MED-3: download with rate below floor aborts after grace period."""
    from unittest.mock import patch

    meta = _metadata(size_kb=100, sha="a" * 64)
    payload = b"\x00" * (100 * 1024)

    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        respx.get("https://civitai.com/cdn/file.safetensors").respond(200, content=payload)

        # Reduce the grace period + floor so the test doesn't wait 30s.
        import app.loras.civitai as cv

        with (
            patch.object(cv, "_THROUGHPUT_GRACE_SECONDS", 0.0),
            patch.object(cv, "_MIN_THROUGHPUT_BYTES_PER_SEC", 10**12),
        ):
            fetcher = _fetcher(store, http, loras_root)
            row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
            await _run_and_wait(fetcher, store, row.id)

    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "civitai_unavailable"
    assert "slow download" in final.error_message


async def test_mid_stream_disk_recheck_aborts(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    """MED-4: `sizeKB` says small, actual download is much larger; mid-stream
    disk check aborts before filling the disk."""
    from unittest.mock import patch

    import app.loras.civitai as cv

    # 20 MiB payload, metadata claims 2 MiB (expected_size inflated so the
    # remaining-bytes math still wants more disk than the stub reports free).
    payload = b"\x00" * (20 * 1024 * 1024)
    sha = hashlib.sha256(payload).hexdigest()
    meta = _metadata(size_kb=20 * 1024, sha=sha)

    def tiny_free(p):
        return shutil._ntuple_diskusage(1_000_000_000, 999_999_900, 100)

    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        respx.get("https://civitai.com/cdn/file.safetensors").respond(200, content=payload)
        # Pre-check also calls shutil.disk_usage — we need it to pass on entry.
        # Stub only the module-level ref that _stream_download uses.
        with patch.object(cv, "_DISK_RECHECK_EVERY_BYTES", 1024 * 1024):
            orig = cv.shutil.disk_usage
            calls = {"n": 0}

            def counted(p):
                calls["n"] += 1
                # First call is the pre-check in _fetch — let it pass.
                if calls["n"] == 1:
                    return orig(p)
                return tiny_free(p)

            with patch.object(cv.shutil, "disk_usage", counted):
                fetcher = _fetcher(store, http, loras_root, file_max_bytes=50 * 1024 * 1024)
                row = await create_pending(
                    store, url="x", civitai_model_id=123, civitai_version_id=456
                )
                await _run_and_wait(fetcher, store, row.id)

    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "insufficient_storage"


async def test_shutdown_cancels_mid_fetch_shield_writes_handover(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    """LOW-6: fetcher.close() cancels a mid-download task; the handover
    set_failed still completes via asyncio.shield."""
    payload = b"\x00" * 4096
    sha = hashlib.sha256(payload).hexdigest()
    meta = _metadata(size_kb=4, sha=sha)

    import asyncio as _asyncio

    release = _asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, content=payload)

    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        respx.get("https://civitai.com/cdn/file.safetensors").mock(side_effect=slow)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        fetcher.enqueue(row.id)
        await _asyncio.sleep(0.2)  # let the task start
        await fetcher.close()
        release.set()

    final = await get_by_id(store, row.id)
    assert final.status == "failed"
    assert final.error_code == "service_restarted"
    assert final.handover is True


async def test_version_lock_pruned_after_fetch(
    store: JobStore, http: httpx.AsyncClient, loras_root: Path
) -> None:
    """LOW-9: fetcher's per-version lock dict is pruned after completion."""
    payload = b"\x00" * 512
    sha = hashlib.sha256(payload).hexdigest()
    meta = _metadata(size_kb=1, sha=sha)
    with respx.mock:
        respx.get("https://civitai.com/api/v1/model-versions/456").respond(json=meta)
        respx.get("https://civitai.com/cdn/file.safetensors").respond(200, content=payload)
        fetcher = _fetcher(store, http, loras_root)
        row = await create_pending(store, url="x", civitai_model_id=123, civitai_version_id=456)
        await _run_and_wait(fetcher, store, row.id)

    assert 456 not in fetcher._version_locks  # pruned
