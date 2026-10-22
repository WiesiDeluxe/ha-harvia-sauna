"""Push connections: reconnect gaps, back-off and dead subscriptions (code
review, 22 Oct 2026).

- The periodic reconnect counted the full heartbeat timeout (300 s) per
  keep-alive, so with keep-alives a minute apart each feed reconnected about
  every 6 min instead of every 30 - and every reconnect sat out websockets'
  10 s close_timeout, because the cloud never answers a close frame. Pushes
  arriving in that gap were lost (a lost ignition message: #14).
- Any auth-looking error reset the back-off, so a changed password made each
  feed log in every 1-2 s.
- An "error" frame left the connection idling on keep-alives.
"""
import asyncio
import base64
import contextlib
import hashlib
import importlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets

from custom_components.harvia_sauna.const import (
    WS_AUTH_RETRY_DELAY,
    WS_MAX_RECONNECT_DELAY,
)
from custom_components.harvia_sauna.errors import HarviaAuthError
from custom_components.harvia_sauna.websocket import _reconnect_due, _retry_delay

MODULES = ["websocket", "websocket_harviaio"]
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _frame(message: dict) -> bytes:
    """An unmasked server text frame (payloads here stay below 126 bytes)."""
    payload = json.dumps(message).encode()
    assert len(payload) < 126
    return bytes([0x81, len(payload)]) + payload


async def _appsync_like_server(ka_every: float, ka_count: int, timeout_ms: int):
    """Speaks just enough graphql-ws, and never answers a close frame."""
    handlers: list[asyncio.Task] = []

    async def handle(reader, writer):
        handlers.append(asyncio.current_task())
        request = b""
        while b"\r\n\r\n" not in request:
            request += await reader.read(1024)
        key = next(
            line.split(b":", 1)[1].strip()
            for line in request.split(b"\r\n")
            if line.lower().startswith(b"sec-websocket-key")
        )
        accept = base64.b64encode(hashlib.sha1(key + _GUID.encode()).digest())
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Protocol: graphql-ws\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n"
        )
        writer.write(_frame({"type": "connection_ack",
                             "payload": {"connectionTimeoutMs": timeout_ms}}))
        writer.write(_frame({"type": "start_ack", "id": "x"}))
        await writer.drain()
        with contextlib.suppress(Exception):
            for _ in range(ka_count):
                await asyncio.sleep(ka_every)
                writer.write(_frame({"type": "ka"}))
                await writer.drain()
            while await reader.read(1024):
                pass  # swallow everything, reply to nothing

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    server.handlers = handlers
    return server


async def _shutdown(server) -> None:
    server.close()
    for task in server.handlers:
        task.cancel()
    await asyncio.gather(*server.handlers, return_exceptions=True)


def _connection(module: str, port: int):
    mod = importlib.import_module(f"custom_components.harvia_sauna.{module}")
    api = SimpleNamespace(
        async_get_websocket_info=AsyncMock(return_value={"host": "h"}),
        async_get_id_token=AsyncMock(return_value="token"),
        async_get_websocket_url=AsyncMock(return_value=f"ws://127.0.0.1:{port}/"),
        invalidate_tokens=MagicMock(),
    )
    if module == "websocket":
        ws = mod.HarviaWebSocket(api, "data", "receiver", False, AsyncMock())
        ssl_patch = patch.object(mod.HarviaWebSocket, "_create_ssl_context", return_value=None)
    else:
        ws = mod.HarviaIoWebSocket(api, "data", "receiver", AsyncMock())
        ssl_patch = patch.object(mod.ssl, "create_default_context", return_value=None)
    ws._running = True
    return ws, ssl_patch


@pytest.mark.parametrize("module", MODULES)
async def test_periodic_reconnect_does_not_wait_for_a_close(
    hass, module: str, socket_enabled
) -> None:
    server = await _appsync_like_server(ka_every=0.05, ka_count=100, timeout_ms=5000)
    ws, ssl_patch = _connection(module, server.sockets[0].getsockname()[1])
    try:
        with ssl_patch, patch(
            "custom_components.harvia_sauna.websocket.WS_RECONNECT_INTERVAL", 0.2
        ):
            started = time.monotonic()
            async with asyncio.timeout(5):
                await ws._async_connect_and_listen()
            elapsed = time.monotonic() - started
        assert ws._reconnect_now, "it should have been the periodic reconnect"
        assert elapsed < 1.5, f"reconnect left a {elapsed:.1f}s gap (close handshake)"
        assert ws._websocket is None
    finally:
        await _shutdown(server)


@pytest.mark.parametrize("module", MODULES)
async def test_keepalives_do_not_count_as_heartbeat_timeouts(
    hass, module: str, socket_enabled
) -> None:
    """10 keep-alives in 0.5 s are 0.5 s, not 10 x the 0.5 s timeout."""
    server = await _appsync_like_server(ka_every=0.05, ka_count=10, timeout_ms=500)
    ws, ssl_patch = _connection(module, server.sockets[0].getsockname()[1])
    try:
        with ssl_patch, patch(
            "custom_components.harvia_sauna.websocket.WS_RECONNECT_INTERVAL", 2.0
        ):
            async with asyncio.timeout(5):
                await ws._async_connect_and_listen()  # ends on the heartbeat timeout
        assert not ws._reconnect_now, "keep-alives were counted as 300 s each"
    finally:
        await _shutdown(server)


def test_reconnect_is_due_after_real_time() -> None:
    with patch("custom_components.harvia_sauna.websocket.WS_RECONNECT_INTERVAL", 1800):
        assert not _reconnect_due(time.monotonic() - 1799)
        assert _reconnect_due(time.monotonic() - 1800)


def test_refused_login_waits_long() -> None:
    api = SimpleNamespace(invalidate_tokens=MagicMock())
    assert _retry_delay(api, "data", HarviaAuthError("HTTP 401"), 0) >= WS_AUTH_RETRY_DELAY


def test_refused_token_is_dropped_and_backs_off() -> None:
    api = SimpleNamespace(invalidate_tokens=MagicMock())
    err = Exception("server rejected WebSocket connection")
    err.response = SimpleNamespace(status_code=401)
    delay = _retry_delay(api, "data", err, 3)
    api.invalidate_tokens.assert_called_once()
    assert 8 <= delay <= WS_MAX_RECONNECT_DELAY, "the back-off must keep growing"


def test_other_errors_keep_the_tokens() -> None:
    api = SimpleNamespace(invalidate_tokens=MagicMock())
    assert _retry_delay(api, "data", OSError("timeout"), 0) < 2
    api.invalidate_tokens.assert_not_called()


class _FakeSocket:
    """Delivers queued frames, then reports the connection closed."""

    def __init__(self, frames: list[dict]) -> None:
        self._frames = [json.dumps(f) for f in frames]
        self.send = AsyncMock()

    async def recv(self):
        if not self._frames:
            raise websockets.exceptions.ConnectionClosedOK(None, None)
        return self._frames.pop(0)


async def _listen(module: str, frames: list[dict]):
    ws, _ = _connection(module, 0)
    ws._reconnect_attempts = 5
    socket = _FakeSocket(frames)
    if module == "websocket":
        await ws._async_listen(socket, "h")
    else:
        await ws._async_listen(socket, "h", "token")
    return ws, socket


@pytest.mark.parametrize("module", MODULES)
async def test_backoff_ends_only_with_a_confirmed_subscription(module: str) -> None:
    ws, _ = await _listen(module, [{"type": "connection_ack"}, {"type": "ka"}])
    assert ws._reconnect_attempts == 5, "accepted then dropped: keep backing off"
    ws, _ = await _listen(module, [{"type": "connection_ack"}, {"type": "start_ack"}])
    assert ws._reconnect_attempts == 0


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("kind", ["error", "connection_error"])
async def test_error_frame_ends_the_connection(module: str, kind: str) -> None:
    _, socket = await _listen(module, [
        {"type": "connection_ack"}, {"type": kind, "payload": {"errors": []}},
        {"type": "ka"},
    ])
    assert socket._frames, "it kept listening after the error frame"


@pytest.mark.parametrize("module", MODULES)
async def test_a_failing_connection_does_not_stay_connected(module: str) -> None:
    """_websocket was only cleared on a normal exit; push_connected lied."""
    ws, _ = _connection(module, 0)

    class _Broken:
        transport = MagicMock()
        send = AsyncMock()

        async def recv(self):
            raise RuntimeError("boom")

    @contextlib.asynccontextmanager
    async def connect(*args, **kwargs):
        yield _Broken()

    mod = importlib.import_module(f"custom_components.harvia_sauna.{module}")
    ssl_patch = (
        patch.object(mod.HarviaWebSocket, "_create_ssl_context", return_value=None)
        if module == "websocket"
        else patch.object(mod.ssl, "create_default_context", return_value=None)
    )
    with ssl_patch, patch.object(mod.websockets, "connect", connect), pytest.raises(RuntimeError):
        await ws._async_connect_and_listen()
    assert ws._websocket is None


@pytest.mark.parametrize("module", MODULES)
async def test_refused_token_in_an_error_frame_is_dropped(module: str) -> None:
    ws, _ = await _listen(module, [{"type": "connection_ack"}, {
        "type": "connection_error",
        "payload": {"errors": [{"errorType": "UnauthorizedException", "errorCode": 401}]},
    }])
    ws._api.invalidate_tokens.assert_called_once()


@pytest.mark.parametrize("module", MODULES)
async def test_other_error_frames_keep_the_token(module: str) -> None:
    ws, _ = await _listen(module, [{"type": "connection_ack"}, {
        "type": "error", "payload": {"errors": [{"errorType": "LimitExceeded"}]},
    }])
    ws._api.invalidate_tokens.assert_not_called()


@pytest.mark.parametrize("module", MODULES)
async def test_periodic_reconnect_skips_the_backoff(module: str) -> None:
    ws, _ = _connection(module, 0)
    calls = 0

    async def connect_and_listen():
        nonlocal calls
        calls += 1
        if calls == 1:
            ws._reconnect_now = True  # periodic reconnect
        else:
            ws._running = False

    mod = importlib.import_module(f"custom_components.harvia_sauna.{module}")
    sleep = AsyncMock()
    with patch.object(ws, "_async_connect_and_listen", connect_and_listen), \
            patch.object(mod.asyncio, "sleep", sleep):
        await ws.async_run()
    assert calls == 2
    sleep.assert_not_called()


async def test_leave_aborts_even_when_cancelled_while_sending() -> None:
    from custom_components.harvia_sauna.websocket import _leave

    socket = SimpleNamespace(transport=MagicMock(), send=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await _leave(socket, "sub", running=True)
    socket.transport.abort.assert_called_once()
