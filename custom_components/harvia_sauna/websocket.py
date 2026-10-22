"""WebSocket manager for real-time Harvia Sauna updates."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import time
import uuid
from typing import Any, Callable

import websockets

from .api import HarviaApiClient
from .const import (
    WS_AUTH_RETRY_DELAY,
    WS_HEARTBEAT_TIMEOUT,
    WS_MAX_RECONNECT_DELAY,
    WS_RECONNECT_INTERVAL,
    WS_STOP_TIMEOUT,
)
from .errors import HarviaAuthError

_LOGGER = logging.getLogger(__name__)


async def _bounded(coro) -> None:
    """Await a teardown step, giving up after WS_STOP_TIMEOUT seconds.

    On a half-dead connection send() and close() can wait until the TCP
    stack gives up. Whatever happens here, the caller must be able to move
    on.
    """
    try:
        async with asyncio.timeout(WS_STOP_TIMEOUT):
            await coro
    except Exception:  # noqa: BLE001 - includes TimeoutError, ConnectionClosed
        pass


async def _drop(websocket) -> None:
    """End a connection without waiting for the close handshake.

    Called after the subscription's "stop" has been sent, so a graceful
    close adds nothing - and the cloud does not complete it: on a Fenix every
    close() ran into its full timeout (measured 2026-09-27), which a local
    server that never answers a close frame reproduces in either teardown
    order. Aborting the transport is immediate; the run loop then sees the
    connection closed and ends on its own.
    """
    transport = getattr(websocket, "transport", None)
    if transport is not None:
        transport.abort()
    else:
        await _bounded(websocket.close())


def _is_rejected_token(err: Exception) -> bool:
    """Whether the cloud refused the token (handshake or request 401/403)."""
    response = getattr(err, "response", None)
    if getattr(response, "status_code", None) in (401, 403):
        return True
    text = str(err)
    return "401" in text or "403" in text or "Unauthorized" in text


def _retry_delay(api, label: str, err: Exception, attempts: int) -> float:
    """Back-off before reconnecting a feed after an error.

    Up to 2.11.0b5 every auth-looking error reset the back-off, so a changed
    password made each feed log in every 1-2 s until the reauth flow was
    completed. Now a refused login waits WS_AUTH_RETRY_DELAY (the reauth flow
    started by the poll is the fix), a refused token is dropped so the next
    attempt logs in again, and the exponential back-off keeps growing.
    """
    delay = min(2 ** attempts + random.uniform(0, 1), WS_MAX_RECONNECT_DELAY)
    if isinstance(err, HarviaAuthError):
        _LOGGER.warning(
            "Harvia feed %s: login refused (%s), retrying in %ds",
            label, err, WS_AUTH_RETRY_DELAY,
        )
        return max(delay, WS_AUTH_RETRY_DELAY)
    if _is_rejected_token(err):
        _LOGGER.debug("Harvia feed %s: token refused (%s), logging in again", label, err)
        api.invalidate_tokens()
    else:
        _LOGGER.debug("Harvia feed %s error: %s, reconnecting", label, err)
    return delay


def _reconnect_due(connected_at: float) -> bool:
    """Whether a connection has been up for the periodic reconnect interval.

    Real time: up to 2.11.0b5 each keep-alive added the full heartbeat
    timeout (300 s) whatever time had passed, so with keep-alives about a
    minute apart a feed reconnected every ~6 min instead of every 30.
    """
    return time.monotonic() - connected_at >= WS_RECONNECT_INTERVAL


def _frame_rejects_token(message: dict) -> bool:
    """Whether an error frame says the token was refused.

    AppSync reports a refused token mostly as an error frame
    (UnauthorizedException, errorCode 401), not as an HTTP 401.
    """
    text = json.dumps(message.get("payload", {}))
    return "Unauthorized" in text or "401" in text or "403" in text


async def _leave(websocket, subscription_id: str, running: bool) -> None:
    """Leave a connection at once instead of waiting for a close handshake.

    The cloud never answers a close frame: leaving the `async with` with a
    graceful close waited the full 10 s close_timeout at every reconnect, a
    gap in which pushes were lost (websockets 17.1 against a silent server:
    10.0 s graceful, 0.0 s aborted). A lost state message at ignition left a
    Fenix showing "off" for 42 min (#14).
    """
    try:
        if running:
            await _bounded(
                websocket.send(json.dumps({"id": subscription_id, "type": "stop"}))
            )
    finally:
        # Even when cancelled while sending: a skipped abort would leave the
        # graceful close to `async with`, longer than the unload allows
        await _drop(websocket)


async def _stop_all(connections, tasks) -> None:
    """Stop every connection at once, then end the run loops, all bounded."""
    # return_exceptions: one failing stop must not skip cancelling the loops
    await asyncio.gather(
        *(ws.async_stop() for ws in connections), return_exceptions=True
    )
    for task in tasks:
        task.cancel()
    if tasks:
        try:
            async with asyncio.timeout(WS_STOP_TIMEOUT):
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            _LOGGER.warning(
                "%d websocket task(s) ignored cancellation for %ds; abandoning",
                len(tasks), WS_STOP_TIMEOUT,
            )


class HarviaWebSocketManager:
    """Manages all WebSocket connections to MyHarvia Cloud."""

    def __init__(
        self,
        api: HarviaApiClient,
        on_device_update: Callable[[dict], Any],
    ) -> None:
        """Initialize the WebSocket manager."""
        self._api = api
        self._on_device_update = on_device_update
        self._connections: list[HarviaWebSocket] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def async_start(self) -> None:
        """Start all WebSocket connections."""
        if self._running:
            return

        user_data = await self._api.async_get_user_data()  # may raise: setup retries
        self._running = True

        # 4 WebSocket connections: device + data × organization + user
        connection_configs = [
            ("device", user_data["organizationId"], False),
            ("device", user_data["email"], True),
            ("data", user_data["organizationId"], False),
            ("data", user_data["email"], True),
        ]

        for endpoint, receiver, is_user in connection_configs:
            ws = HarviaWebSocket(
                api=self._api,
                endpoint=endpoint,
                receiver=receiver,
                is_user_receiver=is_user,
                on_message=self._handle_message,
            )
            self._connections.append(ws)
            task = asyncio.create_task(ws.async_run())
            self._tasks.append(task)

        _LOGGER.debug("Started %d WebSocket connections", len(self._connections))

    async def async_stop(self) -> None:
        """Stop all WebSocket connections, taking at most a few seconds."""
        self._running = False
        await _stop_all(self._connections, self._tasks)
        self._connections.clear()
        self._tasks.clear()
        _LOGGER.debug("All WebSocket connections stopped")

    async def _handle_message(self, endpoint: str, message: dict) -> None:
        """Route incoming WebSocket messages."""
        msg_type = message.get("type")

        if msg_type == "data":
            payload_data = message.get("payload", {}).get("data", {})
            await self._on_device_update(payload_data)


class HarviaWebSocket:
    """Single WebSocket connection to MyHarvia AppSync."""

    def __init__(
        self,
        api: HarviaApiClient,
        endpoint: str,
        receiver: str,
        is_user_receiver: bool,
        on_message: Callable,
    ) -> None:
        """Initialize a WebSocket connection."""
        self._api = api
        self._endpoint = endpoint
        self._receiver = receiver
        self._is_user_receiver = is_user_receiver
        self._on_message = on_message
        self._websocket = None
        self._running = False
        self._reconnect_attempts = 0
        self._reconnect_now = False  # periodic reconnect: no back-off
        self._subscription_id = str(uuid.uuid4())
        self._label = (
            f"{endpoint}({'user' if is_user_receiver else 'org'})"
        )

    async def async_run(self) -> None:
        """Main WebSocket loop with automatic reconnection."""
        self._running = True

        while self._running:
            try:
                await self._async_connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as err:  # noqa: BLE001 - every error reconnects
                if not self._running:
                    break
                delay = _retry_delay(self._api, self._label, err, self._reconnect_attempts)
            else:
                delay = min(
                    2 ** self._reconnect_attempts + random.uniform(0, 1),
                    WS_MAX_RECONNECT_DELAY,
                )

            if not self._running:
                break
            if self._reconnect_now:
                self._reconnect_now = False
                continue

            self._reconnect_attempts += 1
            _LOGGER.debug(
                "WebSocket %s reconnecting in %.1fs (attempt %d)",
                self._label, delay, self._reconnect_attempts,
            )
            await asyncio.sleep(delay)

    async def async_stop(self) -> None:
        """Stop the WebSocket connection."""
        self._running = False
        websocket, self._websocket = self._websocket, None
        if websocket:
            stop_payload = {"id": self._subscription_id, "type": "stop"}
            await _bounded(websocket.send(json.dumps(stop_payload)))
            await _drop(websocket)

    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        """Create SSL context (blocking, must run in executor)."""
        ctx = ssl.create_default_context()
        return ctx

    async def _async_connect_and_listen(self) -> None:
        """Connect to WebSocket and listen for messages."""
        ws_info = await self._api.async_get_websocket_info(self._endpoint)
        url = await self._api.async_get_websocket_url(self._endpoint)

        self._subscription_id = str(uuid.uuid4())

        # Create SSL context in executor to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        ssl_context = await loop.run_in_executor(None, self._create_ssl_context)

        async with websockets.connect(
            url, subprotocols=["graphql-ws"], ssl=ssl_context
        ) as websocket:
            self._websocket = websocket
            try:
                await self._async_listen(websocket, ws_info["host"])
            finally:
                try:
                    await _leave(websocket, self._subscription_id, self._running)
                finally:
                    self._websocket = None

    async def _async_listen(self, websocket, host: str) -> None:
        """Receive until the connection ends, fails or is due for a reconnect."""
        await websocket.send(json.dumps({"type": "connection_init"}))
        timeout = WS_HEARTBEAT_TIMEOUT  # replaced by the server's value
        connected_at = time.monotonic()

        while self._running:
            try:
                raw_message = await asyncio.wait_for(
                    websocket.recv(), timeout=timeout
                )
            except asyncio.TimeoutError:
                _LOGGER.debug(
                    "WebSocket %s: no heartbeat in %ds, reconnecting",
                    self._label, timeout,
                )
                return
            except websockets.exceptions.ConnectionClosed as err:
                _LOGGER.debug("WebSocket %s connection closed: %s", self._label, err)
                return

            message = json.loads(raw_message)
            msg_type = message.get("type")

            if msg_type == "ka":
                if _reconnect_due(connected_at):
                    _LOGGER.debug(
                        "WebSocket %s: periodic reconnect after %ds",
                        self._label, WS_RECONNECT_INTERVAL,
                    )
                    self._reconnect_now = True
                    return
            elif msg_type == "connection_ack":
                if message.get("payload"):
                    timeout = message["payload"]["connectionTimeoutMs"] / 1000
                await self._async_create_subscription(websocket, host)
            elif msg_type == "start_ack":
                # Only a confirmed subscription ends the back-off; a server
                # that accepts and then drops the connection keeps it growing
                self._reconnect_attempts = 0
                _LOGGER.debug("WebSocket %s: subscription active", self._label)
            elif msg_type == "data":
                self._reconnect_attempts = 0
                await self._on_message(self._endpoint, message)
            elif msg_type in ("error", "connection_error"):
                # A refused subscription delivers nothing; the connection
                # would otherwise idle on keep-alives until the next reconnect
                _LOGGER.warning(
                    "WebSocket %s error message: %s", self._label, message
                )
                if _frame_rejects_token(message):
                    self._api.invalidate_tokens()
                return
            else:
                _LOGGER.debug(
                    "WebSocket %s unknown message type: %s",
                    self._label, msg_type,
                )

    async def _async_create_subscription(
        self, websocket, host: str
    ) -> None:
        """Create a GraphQL subscription on the WebSocket."""
        id_token = await self._api.async_get_id_token()

        if self._endpoint == "data":
            query_str = (
                "subscription Subscription($receiver: String!) {\n"
                "  onDataUpdates(receiver: $receiver) {\n"
                "    item {\n"
                "      deviceId\n"
                "      timestamp\n"
                "      sessionId\n"
                "      type\n"
                "      data\n"
                "      __typename\n"
                "    }\n"
                "    __typename\n"
                "  }\n"
                "}\n"
            )
        else:  # device
            query_str = (
                "subscription Subscription($receiver: String!) {\n"
                "  onStateUpdated(receiver: $receiver) {\n"
                "    desired\n"
                "    reported\n"
                "    timestamp\n"
                "    receiver\n"
                "    __typename\n"
                "  }\n"
                "}\n"
            )

        subscription_data = {
            "query": query_str,
            "variables": {"receiver": self._receiver},
        }

        payload = {
            "id": self._subscription_id,
            "payload": {
                "data": json.dumps(subscription_data),
                "extensions": {
                    "authorization": {
                        "Authorization": id_token,
                        "host": host,
                        "x-amz-user-agent": "aws-amplify/2.0.5 react-native",
                    }
                },
            },
            "type": "start",
        }

        await websocket.send(json.dumps(payload))
