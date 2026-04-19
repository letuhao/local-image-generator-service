from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from websockets.exceptions import ConnectionClosed

from app.backends.base import (
    ComfyNodeError,
    ComfyTimeoutError,
    ComfyUnreachableError,
)
from app.backends.comfyui import ComfyUIAdapter

HTTP = "http://comfyui:8188"
WS = "ws://comfyui:8188/ws"


class FakeWS:
    """Stand-in for websockets.ClientConnection driven by an asyncio.Queue.

    Tests push JSON strings (or ConnectionClosed) into `events`; the adapter's
    _ws_reader calls `recv()` in a loop and sees them one at a time.
    """

    def __init__(self) -> None:
        self.events: asyncio.Queue[Any] = asyncio.Queue()
        self.closed = False
        self.close_called = 0

    async def recv(self) -> str:
        item = await self.events.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True
        self.close_called += 1


async def _push(ws: FakeWS, obj: dict | BaseException) -> None:
    await ws.events.put(obj if isinstance(obj, BaseException) else json.dumps(obj))


@pytest.fixture
def fake_ws() -> FakeWS:
    return FakeWS()


@pytest.fixture
async def adapter(fake_ws: FakeWS) -> AsyncIterator[ComfyUIAdapter]:
    """Adapter with ws_connect patched to hand out the same fake_ws each call.

    For reconnect tests, override ws_connect_factory via `adapter._ws_connect`.
    """

    async def _factory(url: str) -> FakeWS:
        return fake_ws

    a = ComfyUIAdapter(
        http_url=HTTP,
        ws_url=WS,
        http_timeout_s=5.0,
        poll_interval_ms=50,
        ws_connect=_factory,
    )
    try:
        yield a
    finally:
        await a.close()


# ───────────────────────── submit ─────────────────────────


@respx.mock
async def test_submit_posts_graph_with_client_id(adapter: ComfyUIAdapter) -> None:
    called = respx.post(f"{HTTP}/prompt").mock(
        return_value=httpx.Response(
            200, json={"prompt_id": "pid-123", "number": 0, "node_errors": {}}
        )
    )
    pid = await adapter.submit({"1": {"class_type": "X", "inputs": {}}})
    assert pid == "pid-123"
    assert called.called
    body = json.loads(called.calls[0].request.content)
    assert body["prompt"] == {"1": {"class_type": "X", "inputs": {}}}
    assert "client_id" in body
    assert isinstance(body["client_id"], str) and len(body["client_id"]) > 0


@respx.mock
async def test_submit_raises_comfy_node_error_on_node_errors(adapter: ComfyUIAdapter) -> None:
    respx.post(f"{HTTP}/prompt").mock(
        return_value=httpx.Response(
            200, json={"prompt_id": "", "number": 0, "node_errors": {"1": "missing input"}}
        )
    )
    with pytest.raises(ComfyNodeError):
        await adapter.submit({"1": {"class_type": "X", "inputs": {}}})


@respx.mock
async def test_submit_raises_comfy_unreachable_on_connection_refused(
    adapter: ComfyUIAdapter,
) -> None:
    respx.post(f"{HTTP}/prompt").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ComfyUnreachableError):
        await adapter.submit({"1": {"class_type": "X", "inputs": {}}})


# ───────────────────────── wait_for_completion: WS happy path ─────────────────────────


async def test_wait_for_completion_canonical_ws_event(
    adapter: ComfyUIAdapter, fake_ws: FakeWS
) -> None:
    async def feed() -> None:
        await asyncio.sleep(0.02)
        await _push(fake_ws, {"type": "executing", "data": {"node": None, "prompt_id": "pid-123"}})

    feeder = asyncio.create_task(feed())
    try:
        await adapter.wait_for_completion("pid-123", timeout_s=2.0)
    finally:
        feeder.cancel()


async def test_wait_for_completion_filters_by_prompt_id(
    adapter: ComfyUIAdapter, fake_ws: FakeWS
) -> None:
    """Events carrying a different prompt_id must NOT resolve our future."""

    async def feed() -> None:
        await _push(
            fake_ws, {"type": "executing", "data": {"node": None, "prompt_id": "other-pid"}}
        )
        await _push(
            fake_ws, {"type": "executing", "data": {"node": "42", "prompt_id": "pid-123"}}
        )  # mid-run, not terminal
        await asyncio.sleep(0.05)
        await _push(fake_ws, {"type": "executing", "data": {"node": None, "prompt_id": "pid-123"}})

    feeder = asyncio.create_task(feed())
    try:
        await adapter.wait_for_completion("pid-123", timeout_s=2.0)
    finally:
        feeder.cancel()


# ───────────────────────── wait_for_completion: reconnect + polling fallback ──


@respx.mock
async def test_wait_for_completion_reconnects_ws_once_on_disconnect() -> None:
    ws_a = FakeWS()
    ws_b = FakeWS()
    calls: list[FakeWS] = []

    async def _factory(url: str) -> FakeWS:
        ws = [ws_a, ws_b][len(calls)]
        calls.append(ws)
        return ws

    adapter = ComfyUIAdapter(
        http_url=HTTP,
        ws_url=WS,
        http_timeout_s=5.0,
        poll_interval_ms=50,
        ws_connect=_factory,
    )
    try:

        async def feed() -> None:
            await asyncio.sleep(0.02)
            await _push(ws_a, ConnectionClosed(None, None))  # ws_a dies
            await asyncio.sleep(0.05)
            await _push(ws_b, {"type": "executing", "data": {"node": None, "prompt_id": "pid-ok"}})

        feeder = asyncio.create_task(feed())
        try:
            await adapter.wait_for_completion("pid-ok", timeout_s=2.0)
            assert len(calls) == 2, "expected one reconnect attempt"
        finally:
            feeder.cancel()
    finally:
        await adapter.close()


@respx.mock
async def test_wait_for_completion_falls_back_to_polling_after_reconnect_fails() -> None:
    attempts = 0

    async def _factory(url: str) -> FakeWS:
        nonlocal attempts
        attempts += 1
        raise ConnectionRefusedError("WS refused")

    respx.get(f"{HTTP}/history/pid-poll").mock(
        return_value=httpx.Response(
            200,
            json={
                "pid-poll": {
                    "status": {"completed": True, "status_str": "success"},
                    "outputs": {},
                }
            },
        )
    )
    adapter = ComfyUIAdapter(
        http_url=HTTP,
        ws_url=WS,
        http_timeout_s=5.0,
        poll_interval_ms=50,
        ws_connect=_factory,
    )
    try:
        await adapter.wait_for_completion("pid-poll", timeout_s=2.0)
        # Both the initial connect and one reconnect attempt should have tried.
        assert attempts == 2
    finally:
        await adapter.close()


async def test_wait_for_completion_raises_timeout(adapter: ComfyUIAdapter, fake_ws: FakeWS) -> None:
    """No event ever arrives → ComfyTimeoutError after timeout_s."""
    with pytest.raises(ComfyTimeoutError):
        await adapter.wait_for_completion("pid-never", timeout_s=0.3)


# ───────────────────────── fetch_outputs ─────────────────────────


@respx.mock
async def test_fetch_outputs_reads_all_image_nodes(adapter: ComfyUIAdapter) -> None:
    """Harvests every output node with images[]. ComfyUI's /history doesn't echo
    our _meta anchors back, so anchor-based filtering is neither possible nor
    needed in Cycle 2's single-SaveImage workflows."""
    respx.get(f"{HTTP}/history/pid-x").mock(
        return_value=httpx.Response(
            200,
            json={
                "pid-x": {
                    "status": {"completed": True, "status_str": "success"},
                    "outputs": {
                        "8": {
                            "images": [{"filename": "img.png", "subfolder": "", "type": "output"}]
                        },
                    },
                }
            },
        )
    )
    png_bytes = b"\x89PNG\r\n\x1a\nFAKE"
    respx.get(f"{HTTP}/view").mock(return_value=httpx.Response(200, content=png_bytes))

    outputs = await adapter.fetch_outputs("pid-x")
    assert outputs == [png_bytes]


@respx.mock
async def test_fetch_outputs_raises_on_error_status(adapter: ComfyUIAdapter) -> None:
    """When /history.status.status_str == 'error', we raise instead of harvesting."""
    respx.get(f"{HTTP}/history/pid-fail").mock(
        return_value=httpx.Response(
            200,
            json={
                "pid-fail": {
                    "status": {
                        "completed": True,
                        "status_str": "error",
                        "messages": [["execution_error", {"node_id": "6"}]],
                    },
                    "outputs": {},
                }
            },
        )
    )
    with pytest.raises(ComfyNodeError, match="error"):
        await adapter.fetch_outputs("pid-fail")


@respx.mock
async def test_poll_fallback_raises_on_error_status() -> None:
    """_poll_until_done must treat (completed=True, status_str=error) as failure."""

    async def _factory(url: str) -> FakeWS:
        raise ConnectionRefusedError("WS refused")

    respx.get(f"{HTTP}/history/pid-poll-err").mock(
        return_value=httpx.Response(
            200,
            json={
                "pid-poll-err": {
                    "status": {
                        "completed": True,
                        "status_str": "error",
                        "messages": [["boom"]],
                    },
                    "outputs": {},
                }
            },
        )
    )
    adapter = ComfyUIAdapter(
        http_url=HTTP,
        ws_url=WS,
        http_timeout_s=5.0,
        poll_interval_ms=50,
        ws_connect=_factory,
    )
    try:
        with pytest.raises(ComfyNodeError, match="error"):
            await adapter.wait_for_completion("pid-poll-err", timeout_s=2.0)
    finally:
        await adapter.close()


async def test_wait_for_completion_rejects_duplicate_prompt_id(
    adapter: ComfyUIAdapter, fake_ws: FakeWS
) -> None:
    """A second wait on the same prompt_id must refuse (would otherwise orphan first)."""
    first = asyncio.create_task(adapter.wait_for_completion("pid-dup", timeout_s=5.0))
    await asyncio.sleep(0.05)  # let the first call register in _pending
    try:
        with pytest.raises(RuntimeError, match="already in progress"):
            await adapter.wait_for_completion("pid-dup", timeout_s=0.1)
    finally:
        await _push(fake_ws, {"type": "executing", "data": {"node": None, "prompt_id": "pid-dup"}})
        await first


@respx.mock
async def test_submit_raises_comfy_node_error_on_non_json_serializable_graph(
    adapter: ComfyUIAdapter,
) -> None:
    """A graph containing a Path (or any non-JSON value) → ComfyNodeError, not TypeError."""
    from pathlib import Path

    graph = {"1": {"class_type": "X", "inputs": {"something": Path("/etc/passwd")}}}
    with pytest.raises(ComfyNodeError, match="not JSON-serializable"):
        await adapter.submit(graph)


# ───────────────────────── cancel ─────────────────────────


@respx.mock
async def test_cancel_interrupts_when_prompt_is_running(adapter: ComfyUIAdapter) -> None:
    # /queue returns running=[[number,pid,…]], pending=[] → we hit /interrupt.
    respx.get(f"{HTTP}/queue").mock(
        return_value=httpx.Response(
            200, json={"queue_running": [[0, "pid-run"]], "queue_pending": []}
        )
    )
    interrupt = respx.post(f"{HTTP}/interrupt").mock(return_value=httpx.Response(200))
    delete_q = respx.post(f"{HTTP}/queue").mock(return_value=httpx.Response(200))

    await adapter.cancel("pid-run")
    assert interrupt.called
    assert not delete_q.called


@respx.mock
async def test_cancel_deletes_queue_entry_when_prompt_is_pending(adapter: ComfyUIAdapter) -> None:
    respx.get(f"{HTTP}/queue").mock(
        return_value=httpx.Response(
            200, json={"queue_running": [], "queue_pending": [[0, "pid-pending"]]}
        )
    )
    interrupt = respx.post(f"{HTTP}/interrupt").mock(return_value=httpx.Response(200))
    delete_q = respx.post(f"{HTTP}/queue").mock(return_value=httpx.Response(200))

    await adapter.cancel("pid-pending")
    assert delete_q.called
    assert not interrupt.called


@respx.mock
async def test_cancel_raises_on_queue_non_200(adapter: ComfyUIAdapter) -> None:
    """Previously: silent no-op when /queue was unavailable. Now explicit error."""
    respx.get(f"{HTTP}/queue").mock(return_value=httpx.Response(503, text="nope"))
    with pytest.raises(ComfyUnreachableError, match="/queue returned 503"):
        await adapter.cancel("pid-any")


# ───────────────────────── free + health ─────────────────────────


@respx.mock
async def test_free_posts_unload_flags_then_polls_system_stats(adapter: ComfyUIAdapter) -> None:
    """/free must carry both unload flags; /system_stats must be polled both before
    (baseline) and after (to verify VRAM rose). Simulated rise: 10 GB → 20 GB."""
    post_free = respx.post(f"{HTTP}/free").mock(return_value=httpx.Response(200))
    stats = respx.get(f"{HTTP}/system_stats").mock(
        side_effect=[
            httpx.Response(200, json={"devices": [{"vram_free": 10 * 1024**3}]}),  # baseline
            httpx.Response(200, json={"devices": [{"vram_free": 20 * 1024**3}]}),  # post-free
        ]
    )
    await adapter.free(verify_timeout_s=2.0)
    body = json.loads(post_free.calls[0].request.content)
    assert body == {"unload_models": True, "free_memory": True}
    assert stats.call_count >= 2, "expected baseline + at least one post-free poll"


@respx.mock
async def test_free_logs_warning_when_vram_does_not_rise(adapter: ComfyUIAdapter) -> None:
    """If /system_stats reports the same vram_free before and after /free for the
    whole timeout window, free() logs a warning but returns normally."""
    respx.post(f"{HTTP}/free").mock(return_value=httpx.Response(200))
    respx.get(f"{HTTP}/system_stats").mock(
        return_value=httpx.Response(200, json={"devices": [{"vram_free": 5 * 1024**3}]})
    )
    # Should complete within ~0.5s since verify_timeout_s=0.5.
    await adapter.free(verify_timeout_s=0.5)


async def test_free_rejects_non_positive_poll_interval() -> None:
    """Guard against accidental poll_interval_ms=0 tight loops."""
    with pytest.raises(ValueError, match="poll_interval_ms must be > 0"):
        ComfyUIAdapter(http_url=HTTP, ws_url=WS, poll_interval_ms=0)


@respx.mock
async def test_health_ok_shape(adapter: ComfyUIAdapter) -> None:
    respx.get(f"{HTTP}/system_stats").mock(
        return_value=httpx.Response(
            200, json={"devices": [{"vram_free": 8 * 1024**3, "vram_total": 24 * 1024**3}]}
        )
    )
    h = await adapter.health()
    assert h["status"] == "ok"
    assert h["vram_free_gb"] == pytest.approx(8.0, abs=0.01)


@respx.mock
async def test_health_down_on_connection_refused(adapter: ComfyUIAdapter) -> None:
    respx.get(f"{HTTP}/system_stats").mock(side_effect=httpx.ConnectError("refused"))
    h = await adapter.health()
    assert h["status"] == "down"
