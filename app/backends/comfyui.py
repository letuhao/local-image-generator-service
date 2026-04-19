from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import httpx
import structlog
import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, WebSocketException

from app.backends.base import (
    ComfyNodeError,
    ComfyTimeoutError,
    ComfyUnreachableError,
)

log = structlog.get_logger(__name__)

# Canonical "prompt finished" event: {"type":"executing","data":{"node":null,"prompt_id":<pid>}}.
_EVENT_TYPE = "executing"
_RECONNECT_BACKOFF_S = 1.0
_MAX_RECONNECTS = 1

# Connect-exception types we treat as "WS unavailable" and allow ONE retry on.
# Broad by design: includes the full websockets exception hierarchy (handshake errors
# like InvalidStatus are subclasses of WebSocketException), plain TCP OSErrors, and
# asyncio/builtin TimeoutError. Intentionally does NOT include asyncio.CancelledError.
_WS_CONN_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionRefusedError,
    ConnectionClosed,
    WebSocketException,
    OSError,
    TimeoutError,
)

WSConnectFactory = Callable[[str], Awaitable[Any]]


async def _default_ws_connect(url: str) -> ClientConnection:
    return await websockets.connect(url)


def _raise_if_errored(status: dict) -> None:
    """Inspect a ComfyUI history `status` dict and raise ComfyNodeError on non-success.

    The canonical shape is {status_str: "success"|"error", completed: bool, messages: [...]}.
    If `status_str` is missing (older ComfyUI), treat completed=True as implicit success.
    """
    status_str = status.get("status_str")
    if status_str is not None and status_str != "success":
        messages = status.get("messages") or []
        raise ComfyNodeError(f"ComfyUI terminal error status={status_str!r}: {messages}")


class ComfyUIAdapter:
    """Backend adapter for a pinned ComfyUI sidecar.

    Single client_id per adapter instance (arch §4.3). One long-lived WS connection
    managed lazily; `wait_for_completion` tolerates exactly **one** reconnect before
    falling back to /history polling (Cycle 2 CLARIFY Q4).
    """

    def __init__(
        self,
        *,
        http_url: str,
        ws_url: str,
        http_timeout_s: float = 30.0,
        poll_interval_ms: int = 1000,
        ws_connect: WSConnectFactory | None = None,
    ) -> None:
        if poll_interval_ms <= 0:
            raise ValueError(f"poll_interval_ms must be > 0, got {poll_interval_ms}")
        self._client_id = uuid4().hex
        self._http = httpx.AsyncClient(base_url=http_url, timeout=http_timeout_s)
        self._ws_url_base = ws_url
        self._poll_interval_s = poll_interval_ms / 1000
        self._ws_connect = ws_connect or _default_ws_connect

        self._ws: Any | None = None
        self._ws_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        # Terminal-completion futures keyed by prompt_id.
        self._pending: dict[str, asyncio.Future[None]] = {}

    # ───────────────────────── submit ─────────────────────────

    async def submit(self, graph: dict) -> str:
        body = {"prompt": graph, "client_id": self._client_id}
        try:
            resp = await self._http.post("/prompt", json=body)
        except httpx.ConnectError as exc:
            raise ComfyUnreachableError(f"connect /prompt: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ComfyUnreachableError(f"transport /prompt: {exc}") from exc
        except TypeError as exc:
            # Graph contained a non-JSON-serializable value (e.g. a Path). Caller bug.
            raise ComfyNodeError(f"graph not JSON-serializable: {exc}") from exc

        # /prompt returns 400 with node_errors when the graph fails validation; we treat
        # that as a ComfyNodeError (client bug / workflow mismatch), not unreachable.
        # Any other non-2xx or transport failure is ComfyUnreachableError.
        data: dict[str, Any] | None = None
        if resp.status_code >= 400:
            try:
                data = resp.json()
            except ValueError:
                data = None
            node_errors = (data or {}).get("node_errors") or {}
            if resp.status_code == 400 and node_errors:
                raise ComfyNodeError(f"node_errors from /prompt: {node_errors}")
            raise ComfyUnreachableError(f"/prompt returned {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        node_errors = data.get("node_errors") or {}
        if node_errors:
            raise ComfyNodeError(f"node_errors from /prompt: {node_errors}")
        prompt_id = data.get("prompt_id") or ""
        if not prompt_id:
            raise ComfyNodeError(f"/prompt response missing prompt_id: {data}")
        log.info("comfy.submit", prompt_id=prompt_id, client_id=self._client_id)
        return prompt_id

    # ───────────────────────── wait_for_completion ─────────────────────────

    async def wait_for_completion(self, prompt_id: str, timeout_s: float) -> None:
        """Block until the prompt reaches terminal state.

        Strategy (Cycle 2 CLARIFY Q4):
          1. Try WS connect. If that fails, sleep 1s and retry once. If both fail,
             fall through to polling until deadline.
          2. WS up: wait for the terminal future OR the reader-task to complete.
             Reader completing without future resolving == WS disconnect mid-job.
             Use our one reconnect budget; on second disconnect, fall back to polling.
          3. Any ComfyTimeoutError is raised if the overall deadline elapses.
        """
        deadline = time.monotonic() + timeout_s
        if prompt_id in self._pending:
            # Defensive: two concurrent waits on the same prompt_id would silently
            # overwrite each other's future. Caller bug (Cycle 4's queue/disconnect
            # handler could trip this if not careful).
            raise RuntimeError(
                f"wait_for_completion already in progress for prompt_id={prompt_id!r}"
            )
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending[prompt_id] = fut
        reconnects_used = 0

        try:
            # Phase 1 — initial connect with one retry, else polling.
            if not await self._try_connect(attempts=2):
                await self._poll_until_done(prompt_id, deadline)
                return

            # Phase 2 — wait for future OR reader death, with reconnect budget.
            while not fut.done():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ComfyTimeoutError(
                        f"wait_for_completion({prompt_id}) exceeded {timeout_s}s"
                    )
                current_reader = self._reader_task
                wait_set = {fut}
                if current_reader is not None:
                    wait_set.add(current_reader)
                done, _pending = await asyncio.wait(
                    wait_set, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    raise ComfyTimeoutError(
                        f"wait_for_completion({prompt_id}) exceeded {timeout_s}s"
                    )
                if fut in done:
                    return

                # Reader task finished first → WS died before completion event arrived.
                if reconnects_used >= _MAX_RECONNECTS:
                    await self._poll_until_done(prompt_id, deadline)
                    return
                reconnects_used += 1
                await asyncio.sleep(_RECONNECT_BACKOFF_S)
                if not await self._try_connect(attempts=1):
                    await self._poll_until_done(prompt_id, deadline)
                    return
        finally:
            self._pending.pop(prompt_id, None)

    async def _try_connect(self, attempts: int) -> bool:
        """Attempt up to `attempts` WS connects with 1s backoff between tries.

        Returns True on success, False if all attempts raised a _WS_CONN_ERRORS.
        """
        for i in range(attempts):
            if i > 0:
                await asyncio.sleep(_RECONNECT_BACKOFF_S)
            try:
                await self._ensure_ws()
                return True
            except _WS_CONN_ERRORS as exc:
                log.info("comfy.ws.connect_failed", attempt=i + 1, of=attempts, error=str(exc))
                continue
        return False

    async def _poll_until_done(self, prompt_id: str, deadline: float) -> None:
        """Fallback when WS is unusable: poll /history until terminal or deadline."""
        log.info("comfy.poll.start", prompt_id=prompt_id)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ComfyTimeoutError(f"polling for {prompt_id} exceeded deadline")
            try:
                resp = await self._http.get(f"/history/{prompt_id}")
            except httpx.HTTPError as exc:
                raise ComfyUnreachableError(f"poll /history: {exc}") from exc
            if resp.status_code == 200:
                data = resp.json()
                entry = data.get(prompt_id)
                if entry and (entry.get("status") or {}).get("completed") is True:
                    # ComfyUI reports completed=True on both success AND error terminals.
                    # Discriminate via status_str; anything other than "success" raises.
                    _raise_if_errored(entry.get("status") or {})
                    return
            await asyncio.sleep(min(self._poll_interval_s, max(0.01, remaining)))

    # ───────────────────────── WS lifecycle ─────────────────────────

    async def _ensure_ws(self) -> None:
        """Connect WS + start reader task. Raises _WS_CONN_ERRORS on failure."""
        async with self._ws_lock:
            if (
                self._ws is not None
                and self._reader_task is not None
                and not self._reader_task.done()
            ):
                return
            url = f"{self._ws_url_base}?clientId={self._client_id}"
            self._ws = await self._ws_connect(url)
            self._reader_task = asyncio.create_task(self._ws_reader(), name="comfy-ws-reader")

    async def _ws_reader(self) -> None:
        """Read WS messages, resolve pending futures on terminal events."""
        assert self._ws is not None
        ws = self._ws
        try:
            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    continue
                try:
                    event = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != _EVENT_TYPE:
                    continue
                data = event.get("data") or {}
                if data.get("node") is not None:
                    continue  # still mid-execution
                pid = data.get("prompt_id")
                fut = self._pending.get(pid) if pid else None
                if fut is not None and not fut.done():
                    fut.set_result(None)
        except ConnectionClosed:
            log.info("comfy.ws.closed")
        except Exception as exc:  # pragma: no cover — defensive
            log.exception("comfy.ws.reader_crashed", error=str(exc))
        finally:
            self._ws = None

    # ───────────────────────── fetch_outputs ─────────────────────────

    async def fetch_outputs(self, prompt_id: str) -> list[bytes]:
        try:
            resp = await self._http.get(f"/history/{prompt_id}")
        except httpx.HTTPError as exc:
            raise ComfyUnreachableError(f"GET /history: {exc}") from exc
        if resp.status_code != 200:
            raise ComfyUnreachableError(f"/history returned {resp.status_code}")
        data = resp.json()
        entry = data.get(prompt_id) or {}
        # Discriminate success/error before trying to harvest images. Same guard as
        # _poll_until_done; catches the WS-path + caller-after-poll-path equivalents.
        _raise_if_errored(entry.get("status") or {})
        outputs = entry.get("outputs") or {}

        images_bytes: list[bytes] = []
        for _node_id, node_output in outputs.items():
            for image in node_output.get("images") or []:
                params: dict[str, str] = {
                    "filename": image.get("filename", ""),
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                }
                try:
                    view_resp = await self._http.get("/view", params=params)
                except httpx.HTTPError as exc:
                    raise ComfyUnreachableError(f"GET /view: {exc}") from exc
                if view_resp.status_code != 200:
                    raise ComfyUnreachableError(f"/view returned {view_resp.status_code}")
                images_bytes.append(view_resp.content)
        return images_bytes

    # ───────────────────────── cancel ─────────────────────────

    async def cancel(self, prompt_id: str) -> None:
        """Interrupt if running, DELETE /queue if pending, no-op if neither."""
        try:
            queue_resp = await self._http.get("/queue")
        except httpx.HTTPError as exc:
            raise ComfyUnreachableError(f"GET /queue: {exc}") from exc
        if queue_resp.status_code != 200:
            raise ComfyUnreachableError(
                f"/queue returned {queue_resp.status_code}: {queue_resp.text[:200]}"
            )
        queue_data = queue_resp.json()
        running_pids = {e[1] for e in queue_data.get("queue_running") or [] if len(e) > 1}
        pending_pids = {e[1] for e in queue_data.get("queue_pending") or [] if len(e) > 1}

        if prompt_id in running_pids:
            try:
                await self._http.post("/interrupt")
            except httpx.HTTPError as exc:
                raise ComfyUnreachableError(f"POST /interrupt: {exc}") from exc
            return
        if prompt_id in pending_pids:
            try:
                await self._http.post("/queue", json={"delete": [prompt_id]})
            except httpx.HTTPError as exc:
                raise ComfyUnreachableError(f"DELETE /queue: {exc}") from exc
            return
        log.info("comfy.cancel.noop", prompt_id=prompt_id)

    # ───────────────────────── free + health ─────────────────────────

    async def free(self, verify_timeout_s: float = 10.0) -> None:
        """POST /free then poll /system_stats until VRAM free has risen (or timeout).

        Spec §11.3 requires verifying VRAM actually dropped before the next submit.
        Take a baseline, issue /free, then poll for up to verify_timeout_s waiting
        for vram_free to increase. If it doesn't, log but do NOT raise — VRAM
        reporting via /system_stats is advisory (torch's caching allocator defers
        actual releases); Cycle 7's VRAM guard inspects health separately.
        """
        baseline = await self._read_vram_free()

        try:
            await self._http.post("/free", json={"unload_models": True, "free_memory": True})
        except httpx.HTTPError as exc:
            raise ComfyUnreachableError(f"POST /free: {exc}") from exc

        deadline = time.monotonic() + verify_timeout_s
        last_seen = baseline
        while time.monotonic() < deadline:
            current = await self._read_vram_free()
            if current is not None and (baseline is None or current > baseline):
                log.info(
                    "comfy.free.verified",
                    baseline_gb=(baseline / (1024**3)) if baseline else None,
                    current_gb=current / (1024**3),
                )
                return
            last_seen = current
            await asyncio.sleep(0.5)
        log.warning(
            "comfy.free.no_vram_change",
            baseline_gb=(baseline / (1024**3)) if baseline else None,
            last_gb=(last_seen / (1024**3)) if last_seen else None,
            timeout_s=verify_timeout_s,
        )

    async def _read_vram_free(self) -> int | None:
        """Read current VRAM free (bytes) from /system_stats. None on error."""
        try:
            resp = await self._http.get("/system_stats")
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        stats = resp.json() or {}
        devices = stats.get("devices") or [{}]
        vram_free = devices[0].get("vram_free")
        return int(vram_free) if vram_free is not None else None

    async def health(self) -> dict:
        try:
            resp = await self._http.get("/system_stats")
        except httpx.HTTPError as exc:
            return {"status": "down", "reason": str(exc)}
        if resp.status_code != 200:
            return {"status": "down", "reason": f"/system_stats {resp.status_code}"}
        stats = resp.json() or {}
        devices = stats.get("devices") or [{}]
        vram_free = devices[0].get("vram_free", 0)
        return {"status": "ok", "vram_free_gb": vram_free / (1024**3)}

    # ───────────────────────── teardown ─────────────────────────

    async def close(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass  # expected after cancel()
            except Exception as exc:  # pragma: no cover — defensive
                log.warning("comfy.close.reader_error", error=str(exc))
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:  # pragma: no cover
                log.warning("comfy.close.ws_error", error=str(exc))
            self._ws = None
        await self._http.aclose()
