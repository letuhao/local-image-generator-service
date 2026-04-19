"""Civitai fetcher — spec §8.1.

Downloads a Civitai LoRA to `./loras/civitai/<slug>_<version_id>.safetensors`
with SHA-256 verification and a full JSON sidecar. Async-first: the handler
returns 202 immediately; this class owns the background fetch work under a
semaphore + per-version lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.loras.civitai_url import API_HOST, sanitize_slug
from app.loras.eviction import InsufficientStorageError, evict_for
from app.queue.fetches import (
    LoraFetch,
    get_by_id,
    set_dest_name,
    set_failed,
    set_progress,
    set_status,
    set_total_bytes,
)
from app.queue.store import JobStore

log = structlog.get_logger(__name__)

_CHUNK_SIZE = 1 * 1024 * 1024  # 1 MiB
_PROGRESS_UPDATE_EVERY = 4 * 1024 * 1024  # 4 MiB

# Slow-loris defense — after a warmup grace period, abort any download whose
# average byte rate drops below this floor. The per-chunk read timeout resets
# each time a byte arrives; without this check, a server sending 1 byte every
# 29 s could hold the single fetcher slot for the full 1800 s overall deadline.
_MIN_THROUGHPUT_BYTES_PER_SEC = 10 * 1024  # 10 KiB/s
_THROUGHPUT_GRACE_SECONDS = 30.0
# Mid-stream disk-space re-check cadence — `sizeKB` from metadata can be wrong.
# Re-check free disk at this interval so we abort cleanly instead of letting
# the OS OSError on `f.write`.
_DISK_RECHECK_EVERY_BYTES = 16 * 1024 * 1024  # 16 MiB
# Headroom multiplier (same as the pre-check): need 2× incoming free to allow
# for temp + verify overhead.
_DISK_RECHECK_HEADROOM_MULTIPLIER = 2

# SSRF defense: Civitai's metadata embeds a `downloadUrl` we have to GET. If
# that metadata is ever compromised or tampered with, it could point the fetcher
# at an internal service (minio:9000, comfyui:8188, 169.254.169.254/…). We
# validate the initial URL's scheme + hostname before streaming; httpx's own
# redirect handling refuses https→http downgrades. All real Civitai download
# URLs start at civitai.com and redirect to civitai-delivery-*.{com,amazonaws.com}
# CDN hosts.
_DOWNLOAD_ALLOWED_HOSTS: frozenset[str] = frozenset({"civitai.com", "civitai.red"})
_DOWNLOAD_ALLOWED_SUFFIXES: tuple[str, ...] = (
    ".civitai.com",  # e.g. civitai-delivery-worker-prod-2024-XX-XX.civitai.com
    ".civitai.red",
)


def _validate_download_url(url: str) -> None:
    """Reject download URLs that could target internal services.

    Rules:
      - Scheme must be `https` (no http → plaintext + no http → internal).
      - Hostname must be `civitai.com`/`civitai.red` exactly, or a subdomain
        of those (for the CDN, typically `civitai-delivery-*.civitai.com`).
      - No userinfo, no explicit port.

    Raises `ValueError` on any violation.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"downloadUrl scheme must be https, got {parsed.scheme!r}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("downloadUrl must not contain userinfo")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"downloadUrl has invalid port: {exc}") from exc
    if port is not None:
        raise ValueError("downloadUrl must not specify a port")
    host = (parsed.hostname or "").lower()
    if host in _DOWNLOAD_ALLOWED_HOSTS:
        return
    if any(host.endswith(suffix) for suffix in _DOWNLOAD_ALLOWED_SUFFIXES):
        return
    raise ValueError(f"downloadUrl host {host!r} is not in the Civitai allowlist")


def _metadata_retryable(exc: BaseException) -> bool:
    """Retry on 5xx + connection/timeout errors; surface 4xx immediately."""
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError))


class CivitaiFetcher:
    def __init__(
        self,
        *,
        store: JobStore,
        loras_root: Path,
        api_token: str | None,
        http_client: httpx.AsyncClient,
        dir_max_bytes: int,
        file_max_bytes: int,
        recent_use_days: int,
        max_concurrent: int = 1,
        metadata_timeout_s: float = 30.0,
        download_overall_timeout_s: float = 1800.0,
        chunk_read_timeout_s: float = 30.0,
    ) -> None:
        self._store = store
        self._loras_root = loras_root
        self._api_token = api_token
        self._client = http_client
        self._dir_max_bytes = dir_max_bytes
        self._file_max_bytes = file_max_bytes
        self._recent_use_days = recent_use_days
        self._metadata_timeout_s = metadata_timeout_s
        self._download_overall_timeout_s = download_overall_timeout_s
        self._chunk_read_timeout_s = chunk_read_timeout_s
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._version_locks: dict[int, asyncio.Lock] = {}
        self._locks_mutex = asyncio.Lock()
        self._active_tasks: set[asyncio.Task] = set()

    async def _lock_for_version(self, version_id: int) -> asyncio.Lock:
        async with self._locks_mutex:
            lock = self._version_locks.get(version_id)
            if lock is None:
                lock = asyncio.Lock()
                self._version_locks[version_id] = lock
            return lock

    def enqueue(self, request_id: str) -> None:
        """Spawn a background task for this request. Returns immediately."""
        task = asyncio.create_task(self._run(request_id), name=f"civitai-fetch-{request_id}")
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def close(self) -> None:
        """Cancel all in-flight fetches. Called from lifespan shutdown.

        Swallows any exception so one misbehaving task doesn't block
        shutdown of the others.
        """
        for task in list(self._active_tasks):
            task.cancel()
        for task in list(self._active_tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.warning("civitai.fetch.shutdown_task_error", error=str(exc))

    async def _run(self, request_id: str) -> None:
        """Orchestrator: acquire semaphore + per-version lock, then fetch."""
        row = await get_by_id(self._store, request_id)
        if row is None:
            log.warning("civitai.fetch.unknown_id", request_id=request_id)
            return

        async with self._semaphore:
            lock = await self._lock_for_version(row.civitai_version_id)
            try:
                async with lock:
                    try:
                        await self._fetch(row)
                    except asyncio.CancelledError:
                        # Lifespan shutdown. Flip to failed-with-handover, but
                        # shield the DB write so a second cancel (or a shutdown
                        # timeout) doesn't interrupt the UPDATE mid-flight and
                        # leave the row stuck in a non-terminal state.
                        await asyncio.shield(
                            set_failed(
                                self._store,
                                request_id,
                                error_code="service_restarted",
                                error_message="fetcher cancelled during shutdown",
                                handover=True,
                            )
                        )
                        raise
                    except Exception as exc:
                        log.exception("civitai.fetch.unexpected", request_id=request_id)
                        await set_failed(
                            self._store,
                            request_id,
                            error_code="internal",
                            error_message=str(exc),
                        )
            finally:
                # Prune the per-version lock so the dict doesn't grow unbounded
                # across process lifetime. Safe: we hold the semaphore, so no
                # concurrent waiter can have acquired/be-waiting-on this lock.
                # If someone's about to call _lock_for_version, they'll create
                # a fresh lock — semantically identical for a new fetch.
                await self._maybe_prune_lock(row.civitai_version_id)

    async def _maybe_prune_lock(self, version_id: int) -> None:
        async with self._locks_mutex:
            lock = self._version_locks.get(version_id)
            if lock is not None and not lock.locked():
                del self._version_locks[version_id]

    async def _fetch(self, row: LoraFetch) -> None:
        """Full fetch: metadata → pre-download checks → stream → verify → sidecar."""
        started = time.perf_counter()
        await set_status(self._store, row.id, "downloading")

        # 1. Metadata
        try:
            metadata = await self._fetch_metadata(row.civitai_version_id)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403):
                await set_failed(
                    self._store,
                    row.id,
                    error_code="civitai_auth",
                    error_message=f"{code} from civitai metadata",
                )
            elif code == 404:
                await set_failed(
                    self._store,
                    row.id,
                    error_code="civitai_version_not_found",
                    error_message="civitai returned 404",
                )
            else:
                await set_failed(
                    self._store,
                    row.id,
                    error_code="civitai_unavailable",
                    error_message=f"civitai returned {code}",
                )
            return
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            await set_failed(
                self._store,
                row.id,
                error_code="civitai_unavailable",
                error_message=f"civitai transport failure: {exc}",
            )
            return

        # 2. Extract primary file entry from metadata.
        try:
            file_entry = _pick_primary_file(metadata)
        except ValueError as exc:
            await set_failed(
                self._store,
                row.id,
                error_code="validation_error",
                error_message=str(exc),
            )
            return

        expected_size = int(file_entry.get("sizeKB", 0) * 1024)
        if expected_size > self._file_max_bytes:
            await set_failed(
                self._store,
                row.id,
                error_code="lora_too_large",
                error_message=(f"file size {expected_size} exceeds cap {self._file_max_bytes}"),
            )
            return
        if expected_size > 0:
            await set_total_bytes(self._store, row.id, expected_size)

        # 3. Derive destination path.
        slug = sanitize_slug(file_entry["name"])
        canonical_name = f"civitai/{slug}_{row.civitai_version_id}"
        dest = self._loras_root / "civitai" / f"{slug}_{row.civitai_version_id}.safetensors"
        sidecar_path = dest.with_suffix(".json")
        dest.parent.mkdir(parents=True, exist_ok=True)

        sha_expected = file_entry["hashes"]["SHA256"].lower()

        # 4. Idempotency: if destination already exists, short-circuit.
        if dest.is_file():
            # Backfill sidecar if missing (retroactive metadata attach).
            if not sidecar_path.is_file():
                _write_sidecar_atomic(
                    sidecar_path,
                    canonical_name=canonical_name,
                    sha256=sha_expected,
                    civitai_model_id=row.civitai_model_id,
                    civitai_version_id=row.civitai_version_id,
                    metadata=metadata,
                )
            await set_dest_name(self._store, row.id, canonical_name)
            await set_status(self._store, row.id, "verifying")
            await set_status(self._store, row.id, "done")
            log.info(
                "civitai.fetch.idempotent",
                request_id=row.id,
                name=canonical_name,
            )
            return

        # 5. Disk-space pre-check (2× headroom) + eviction if needed.
        free_bytes = shutil.disk_usage(self._loras_root).free
        needed_with_headroom = expected_size * 2
        if free_bytes < needed_with_headroom:
            try:
                await evict_for(
                    incoming_size=needed_with_headroom,
                    loras_root=self._loras_root,
                    store=self._store,
                    dir_max_bytes=self._dir_max_bytes,
                    recent_use_days=self._recent_use_days,
                )
            except InsufficientStorageError as exc:
                await set_failed(
                    self._store,
                    row.id,
                    error_code="insufficient_storage",
                    error_message=str(exc),
                )
                return

        # 6. Stream download + streaming SHA-256.
        tmp = dest.with_suffix(".safetensors.tmp")
        download_url = file_entry["downloadUrl"]
        try:
            _validate_download_url(download_url)
        except ValueError as exc:
            await set_failed(
                self._store,
                row.id,
                error_code="validation_error",
                error_message=f"unsafe download URL: {exc}",
            )
            return
        try:
            actual_sha = await self._stream_download(row.id, download_url, tmp, expected_size)
        except _DownloadTooLargeError as exc:
            _unlink_quiet(tmp)
            await set_failed(
                self._store,
                row.id,
                error_code="lora_too_large",
                error_message=str(exc),
            )
            return
        except _SlowDownloadError as exc:
            _unlink_quiet(tmp)
            await set_failed(
                self._store,
                row.id,
                error_code="civitai_unavailable",
                error_message=f"slow download aborted: {exc}",
            )
            return
        except _MidStreamDiskFullError as exc:
            _unlink_quiet(tmp)
            await set_failed(
                self._store,
                row.id,
                error_code="insufficient_storage",
                error_message=str(exc),
            )
            return
        except httpx.HTTPStatusError as exc:
            _unlink_quiet(tmp)
            code = exc.response.status_code
            if code in (401, 403):
                await set_failed(
                    self._store,
                    row.id,
                    error_code="civitai_auth",
                    error_message=f"{code} mid-download",
                )
            else:
                await set_failed(
                    self._store,
                    row.id,
                    error_code="civitai_unavailable",
                    error_message=f"download {code}",
                )
            return
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            # httpx.TimeoutException covers Read/Write/Connect/Pool timeouts —
            # widening from the narrower ReadTimeout catches latent slow-write
            # or pool-exhaustion cases we'd previously bubble as "internal".
            _unlink_quiet(tmp)
            await set_failed(
                self._store,
                row.id,
                error_code="civitai_unavailable",
                error_message=f"download transport failure: {exc}",
            )
            return
        except TimeoutError:
            _unlink_quiet(tmp)
            await set_failed(
                self._store,
                row.id,
                error_code="civitai_unavailable",
                error_message=(
                    f"download exceeded overall timeout {self._download_overall_timeout_s}s"
                ),
            )
            return
        except OSError as exc:
            # Disk full, EIO, permission denied, etc. mid-write. Clean up the
            # partial tmp ourselves rather than leaving it for the next boot's
            # recovery sweep, and surface a user-meaningful error code.
            _unlink_quiet(tmp)
            # Heuristic: ENOSPC / "no space left" → insufficient_storage; other
            # OSError → internal (tests may assert on this distinction).
            is_no_space = "No space left" in str(exc) or getattr(exc, "errno", 0) == 28
            await set_failed(
                self._store,
                row.id,
                error_code=("insufficient_storage" if is_no_space else "internal"),
                error_message=f"download write failure: {exc}",
            )
            return

        # 7. Verify SHA.
        await set_status(self._store, row.id, "verifying")
        if actual_sha != sha_expected:
            _unlink_quiet(tmp)
            await set_failed(
                self._store,
                row.id,
                error_code="sha_mismatch",
                error_message=(f"expected {sha_expected} got {actual_sha}"),
            )
            return

        # 8. Write sidecar + atomic rename.
        _write_sidecar_atomic(
            sidecar_path,
            canonical_name=canonical_name,
            sha256=sha_expected,
            civitai_model_id=row.civitai_model_id,
            civitai_version_id=row.civitai_version_id,
            metadata=metadata,
        )
        os.replace(tmp, dest)
        await set_dest_name(self._store, row.id, canonical_name)
        await set_status(self._store, row.id, "done")

        duration_ms = (time.perf_counter() - started) * 1000
        log.info(
            "lora.fetch.ok",
            request_id=row.id,
            url=row.url,
            canonical_name=canonical_name,
            size_bytes=expected_size,
            duration_ms=duration_ms,
        )

    async def _fetch_metadata(self, version_id: int) -> dict[str, Any]:
        """GET /api/v1/model-versions/<vid> with tenacity 3x on 5xx/transport."""
        url = f"https://{API_HOST}/api/v1/model-versions/{version_id}"
        headers: dict[str, str] = {}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
            retry=retry_if_exception(_metadata_retryable),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.get(
                    url, headers=headers, timeout=self._metadata_timeout_s
                )
                resp.raise_for_status()
                return resp.json()
        # unreachable (reraise=True + always either return or raise)
        raise RuntimeError("unreachable")

    async def _stream_download(
        self,
        request_id: str,
        url: str,
        dest_tmp: Path,
        expected_size: int,
    ) -> str:
        """Stream download to dest_tmp, update progress, return hex SHA-256.

        Three safety nets run during the stream:
          1. `_file_max_bytes` cap — refuses a blob that exceeds the hard size
             limit (catches `sizeKB` lies under 2 GiB).
          2. Minimum-throughput — after a 30 s grace period, abort if the
             average byte rate falls below `_MIN_THROUGHPUT_BYTES_PER_SEC`.
             Stops slow-loris attacks on the overall 30-min deadline.
          3. Mid-stream disk re-check — every 16 MiB, re-probe
             `shutil.disk_usage(...).free` and abort if we're about to run out
             (guards against `sizeKB` lies that push us past physical disk).
        """
        timeout = httpx.Timeout(
            connect=10.0,
            read=self._chunk_read_timeout_s,
            write=30.0,
            pool=5.0,
        )
        hasher = hashlib.sha256()
        written = 0
        last_progress_update = 0
        last_disk_check = 0
        started = time.perf_counter()

        async with asyncio.timeout(self._download_overall_timeout_s):
            async with self._client.stream(
                "GET", url, follow_redirects=True, timeout=timeout
            ) as resp:
                resp.raise_for_status()
                # Pre-allocate parent just in case tmp parent was unlinked mid-flight.
                dest_tmp.parent.mkdir(parents=True, exist_ok=True)
                with dest_tmp.open("wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                        if not chunk:
                            continue
                        if self._file_max_bytes and written + len(chunk) > self._file_max_bytes:
                            raise _DownloadTooLargeError(
                                f"streamed {written + len(chunk)} bytes "
                                f"exceeds cap {self._file_max_bytes}"
                            )
                        f.write(chunk)
                        hasher.update(chunk)
                        written += len(chunk)

                        # Throughput floor check (slow-loris defense).
                        elapsed = time.perf_counter() - started
                        if elapsed > _THROUGHPUT_GRACE_SECONDS:
                            rate = written / elapsed
                            if rate < _MIN_THROUGHPUT_BYTES_PER_SEC:
                                raise _SlowDownloadError(
                                    f"throughput {rate:.0f} B/s below floor "
                                    f"{_MIN_THROUGHPUT_BYTES_PER_SEC} B/s after "
                                    f"{elapsed:.0f}s"
                                )

                        # Mid-stream disk check (defends against sizeKB lies).
                        if written - last_disk_check >= _DISK_RECHECK_EVERY_BYTES:
                            last_disk_check = written
                            remaining = max(0, expected_size - written)
                            free = shutil.disk_usage(dest_tmp.parent).free
                            if free < remaining * _DISK_RECHECK_HEADROOM_MULTIPLIER:
                                raise _MidStreamDiskFullError(
                                    f"disk free {free} < remaining "
                                    f"{remaining} x headroom "
                                    f"{_DISK_RECHECK_HEADROOM_MULTIPLIER}"
                                )

                        if written - last_progress_update >= _PROGRESS_UPDATE_EVERY:
                            await set_progress(self._store, request_id, written)
                            last_progress_update = written
        # Final progress flush.
        await set_progress(self._store, request_id, written)
        return hasher.hexdigest()


class _DownloadTooLargeError(Exception):
    """Raised from _stream_download when streamed bytes exceed file_max_bytes."""


class _SlowDownloadError(Exception):
    """Raised when a download's byte rate falls below _MIN_THROUGHPUT_BYTES_PER_SEC
    after the grace period."""


class _MidStreamDiskFullError(Exception):
    """Raised when mid-stream disk-space recheck shows free space is about to
    be exhausted. Lets us unlink the tmp file ourselves with a clean error code
    instead of letting the OS surface an OSError later in the write path."""


def _pick_primary_file(metadata: dict) -> dict:
    """Return the file marked primary=true. Validates shape + SHA256 presence."""
    files = metadata.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("civitai response has no files[]")
    primary = next(
        (f for f in files if isinstance(f, dict) and f.get("primary")),
        None,
    )
    if primary is None:
        raise ValueError("civitai response has no files[].primary=true entry")
    name = primary.get("name")
    if not isinstance(name, str) or not name.endswith(".safetensors"):
        raise ValueError(f"primary file name must end with .safetensors, got {name!r}")
    url = primary.get("downloadUrl")
    if not isinstance(url, str) or not url:
        raise ValueError("primary file missing downloadUrl")
    hashes = primary.get("hashes")
    if not isinstance(hashes, dict):
        raise ValueError("primary file missing hashes")
    sha = hashes.get("SHA256")
    if not isinstance(sha, str) or not sha:
        raise ValueError("file missing SHA256 hash; cannot verify")
    return primary


def _write_sidecar_atomic(
    sidecar_path: Path,
    *,
    canonical_name: str,
    sha256: str,
    civitai_model_id: int | None,
    civitai_version_id: int,
    metadata: dict,
) -> None:
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    trained_words = metadata.get("trainedWords")
    if not isinstance(trained_words, list):
        trained_words = []
    else:
        trained_words = [w for w in trained_words if isinstance(w, str)]
    payload = {
        "name": canonical_name,
        "filename": f"{canonical_name}.safetensors",
        "sha256": sha256,
        "source": "civitai",
        "civitai_model_id": civitai_model_id,
        "civitai_version_id": civitai_version_id,
        "base_model_hint": (
            metadata.get("baseModel") if isinstance(metadata.get("baseModel"), str) else None
        ),
        "trigger_words": trained_words,
        "fetched_at": datetime.now(UTC).isoformat(),
        "last_used": None,
    }
    tmp = sidecar_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, sidecar_path)


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
