"""WebSocket subscriptions for Harvia documented GraphQL feeds."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import random
import ssl
import time
import uuid
from typing import Any, Awaitable, Callable

import websockets

from .api_harviaio import HarviaIoApiClient, _normalize_state_payload, _normalize_telemetry_payload
from .const import (
    WS_HEARTBEAT_TIMEOUT,
    WS_MAX_RECONNECT_DELAY,
)
from .websocket import (
    _bounded,
    _drop,
    _frame_rejects_token,
    _leave,
    _reconnect_due,
    _retry_delay,
    _stop_all,
)

_LOGGER = logging.getLogger(__name__)


class HarviaIoWebSocketManager:
    """Manage Harvia GraphQL feed subscriptions."""

    def __init__(
        self,
        api: HarviaIoApiClient,
        on_device_update: Callable[[dict], Awaitable[None]],
    ) -> None:
        """Initialize websocket manager."""
        self._api = api
        self._on_device_update = on_device_update
        self._connections: list[HarviaIoWebSocket] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False
        # Ring buffer of recent raw feed messages for diagnostics
        self.raw_messages: deque = deque(maxlen=10)

    async def async_start(self) -> None:
        """Start data and device feed subscriptions."""
        if self._running:
            _LOGGER.debug("WebSocket manager already running")
            return

        # May raise: setup is then retried. Logging and returning here left
        # the entry running on 5-minute polls only, with no retry until a reload.
        receiver = await self._api.async_get_receiver_id()
        if receiver is None:
            _LOGGER.info("No Harvia devices on this account - push not started")
            return
        _LOGGER.debug("Starting WebSocket subscriptions for receiver: %s", receiver)

        self._running = True

        configs = [("data", receiver), ("device", receiver)]
        for endpoint, target_receiver in configs:
            ws = HarviaIoWebSocket(
                api=self._api,
                endpoint=endpoint,
                receiver=target_receiver,
                on_message=self._handle_message,
            )
            self._connections.append(ws)
            self._tasks.append(asyncio.create_task(ws.async_run()))
            _LOGGER.debug("Started WebSocket connection for feed: %s", endpoint)

    async def async_stop(self) -> None:
        """Stop all active websocket subscriptions."""
        if not self._running:
            _LOGGER.debug("WebSocket manager not running, nothing to stop")
            return

        _LOGGER.debug("Stopping all WebSocket subscriptions (%d connections)", len(self._connections))
        self._running = False
        await _stop_all(self._connections, self._tasks)
        self._connections.clear()
        self._tasks.clear()
        _LOGGER.debug("WebSocket subscriptions stopped")

    async def _handle_message(self, endpoint: str, message: dict[str, Any]) -> None:
        """Map provider feed payloads into coordinator-compatible update payloads."""
        payload_data = message.get("payload", {}).get("data", {})
        self.raw_messages.append({"endpoint": endpoint, "payload": payload_data})
        if endpoint == "device":
            feed = payload_data.get("devicesStatesUpdateFeed", {})
            item = feed.get("item", {})
            reported = item.get("reported")
            if reported is None:
                return
            
            # Handle case where reported might be a JSON string instead of dict
            if isinstance(reported, str):
                try:
                    reported = json.loads(reported)
                    _LOGGER.debug("Parsed reported as JSON string: %s", reported)
                except (json.JSONDecodeError, TypeError):
                    _LOGGER.warning("Failed to parse reported as JSON string: %s", reported)
                    return
            
            if not isinstance(reported, dict):
                _LOGGER.warning("reported is not a dict: %s (type: %s)", reported, type(reported))
                return
            
            device_id = item.get("deviceId")
            if not device_id:
                _LOGGER.warning("Device state update missing deviceId in item: %s", item)
                return
            
            _LOGGER.debug("Device state update for %s: %s", device_id, reported)
            
            # Normalize reported state to match coordinator expectations
            normalized = _normalize_state_payload(device_id, reported)
            _LOGGER.debug("Normalized device state for %s: %s", device_id, normalized)

            # Pass as JSON string to match coordinator's expected format
            reported_str = json.dumps(normalized)
            update_payload = {"onStateUpdated": {"reported": reported_str}}
            _LOGGER.debug("Forwarding device update to coordinator: %s", update_payload)
            await self._on_device_update(update_payload)
            return

        if endpoint == "data":
            feed = payload_data.get("devicesMeasurementsUpdateFeed", {})
            item = feed.get("item", {})
            data_field = item.get("data")
            if data_field is None:
                _LOGGER.warning("Measurement update missing data field: %s", item)
                return
            
            # Handle case where data might be a JSON string instead of dict
            if isinstance(data_field, str):
                try:
                    data_field = json.loads(data_field)
                    _LOGGER.debug("Parsed data as JSON string: %s", data_field)
                except (json.JSONDecodeError, TypeError):
                    _LOGGER.warning("Failed to parse data as JSON string: %s", data_field)
                    return
            
            device_id = item.get("deviceId")
            if not device_id:
                _LOGGER.warning("Measurement update missing deviceId: %s", item)
                return
            
            _LOGGER.debug("Measurement update for %s: data=%s", device_id, data_field)
            
            # Build telemetry payload with all fields for normalization
            telemetry_payload = {
                "timestamp": item.get("timestamp"),
                "type": item.get("type"),
                "data": data_field if isinstance(data_field, dict) else {}
            }
            
            # Normalize telemetry to match coordinator expectations
            normalized = _normalize_telemetry_payload(telemetry_payload)
            if not set(normalized) - {"timestamp", "type"}:
                # No measurement in it, e.g. the "ESP" sub-feed (heapSize,
                # rssi). It still shows the device is alive, but its
                # timestamp must not count as the latest telemetry frame: a
                # polled cabin frame older than it would be skipped as stale.
                normalized.pop("timestamp", None)
            _LOGGER.debug("Normalized measurement for %s: %s", device_id, normalized)

            update_payload = {
                "onDataUpdates": {
                    "item": {
                        "deviceId": device_id,
                        "timestamp": normalized.get("timestamp"),
                        "data": json.dumps(normalized),
                    }
                }
            }
            _LOGGER.debug("Forwarding telemetry update to coordinator: %s", update_payload)
            await self._on_device_update(update_payload)


class HarviaIoWebSocket:
    """Single GraphQL subscription websocket."""

    def __init__(
        self,
        api: HarviaIoApiClient,
        endpoint: str,
        receiver: str,
        on_message: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Initialize connection state."""
        self._api = api
        self._endpoint = endpoint
        self._receiver = receiver
        self._on_message = on_message
        self._running = False
        self._websocket = None
        self._reconnect_attempts = 0
        self._reconnect_now = False  # periodic reconnect: no back-off
        self._subscription_id = str(uuid.uuid4())
        self._label = endpoint

    async def async_run(self) -> None:
        """Connection loop with reconnection backoff."""
        self._running = True
        while self._running:
            try:
                _LOGGER.debug("Starting Harvia feed %s connection", self._label)
                await self._async_connect_and_listen()
            except asyncio.CancelledError:
                _LOGGER.debug("Harvia feed %s connection cancelled", self._label)
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
                "Harvia feed %s reconnecting in %.1f seconds (attempt %d)",
                self._label,
                delay,
                self._reconnect_attempts,
            )
            await asyncio.sleep(delay)

    async def async_stop(self) -> None:
        """Stop websocket connection (bounded, see websocket._bounded)."""
        self._running = False
        websocket, self._websocket = self._websocket, None
        if websocket is not None:
            await _bounded(
                websocket.send(json.dumps({"id": self._subscription_id, "type": "stop"}))
            )
            await _drop(websocket)

    async def _async_connect_and_listen(self) -> None:
        """Connect and listen to feed updates."""
        ws_info = await self._api.async_get_websocket_info(self._endpoint)
        # Use a single token for both URL and subscription start
        id_token = await self._api.async_get_id_token()
        url = await self._api.async_get_websocket_url(self._endpoint, id_token)

        self._subscription_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        ssl_context = await loop.run_in_executor(None, ssl.create_default_context)

        async with websockets.connect(
            url, subprotocols=["graphql-ws"], ssl=ssl_context
        ) as websocket:
            self._websocket = websocket
            try:
                await self._async_listen(websocket, ws_info["host"], id_token)
            finally:
                try:
                    await _leave(websocket, self._subscription_id, self._running)
                finally:
                    self._websocket = None

    async def _async_listen(self, websocket, host: str, id_token: str) -> None:
        """Receive until the connection ends, fails or is due for a reconnect."""
        await websocket.send(json.dumps({"type": "connection_init"}))
        timeout = WS_HEARTBEAT_TIMEOUT
        connected_at = time.monotonic()
        subscription_active = False

        while self._running:
            try:
                raw_message = await asyncio.wait_for(
                    websocket.recv(), timeout=timeout
                )
            except asyncio.TimeoutError:
                return
            except websockets.exceptions.ConnectionClosed:
                return

            message = json.loads(raw_message)
            msg_type = message.get("type")
            _LOGGER.debug("WebSocket %s received message type=%s payload=%s", self._label, msg_type, str(message)[:200])

            if msg_type == "ka":
                _LOGGER.debug("WebSocket %s keepalive, up %.0fs", self._label, time.monotonic() - connected_at)
                if _reconnect_due(connected_at):
                    _LOGGER.debug("WebSocket %s reconnect interval exceeded, reconnecting", self._label)
                    self._reconnect_now = True
                    return
            elif msg_type == "connection_ack":
                _LOGGER.debug("WebSocket %s connection acknowledged", self._label)
                if message.get("payload"):
                    timeout = message["payload"]["connectionTimeoutMs"] / 1000
                    _LOGGER.debug("WebSocket %s timeout set to %.0f seconds", self._label, timeout)
                await self._async_start_subscription(websocket, host, id_token)
            elif msg_type == "start_ack":
                # Subscription successfully registered, now expecting data.
                # Only now the back-off ends: a server that accepts and
                # then drops the connection keeps it growing.
                subscription_active = True
                self._reconnect_attempts = 0
                _LOGGER.info("WebSocket %s subscription registered and active", self._label)
            elif msg_type == "data":
                if subscription_active:
                    try:
                        _LOGGER.debug("WebSocket %s processing data message: %s", self._label, str(message)[:300])
                        await self._on_message(self._endpoint, message)
                    except Exception as err:
                        _LOGGER.error("Error processing message from feed %s: %s", self._label, err)
                else:
                    _LOGGER.debug("Received data before subscription active on feed %s", self._label)
            elif msg_type == "complete":
                # Subscription completed
                _LOGGER.debug("WebSocket %s subscription completed", self._label)
                return
            elif msg_type in ("error", "connection_error"):
                # A refused subscription delivers nothing; the connection
                # would otherwise idle on keep-alives until the next reconnect
                error_msg = message.get("payload", {})
                _LOGGER.error("Feed %s received error: %s", self._label, error_msg)
                if _frame_rejects_token(message):
                    self._api.invalidate_tokens()
                return
            else:
                _LOGGER.debug("WebSocket %s received unknown message type: %s full_message=%s", self._label, msg_type, message)

    async def _async_start_subscription(self, websocket, host: str, id_token: str) -> None:
        """Send GraphQL subscription start frame."""
        _LOGGER.debug("Starting GraphQL subscription for endpoint: %s", self._endpoint)
        if self._endpoint == "data":
            query_str = (
                "subscription MeasurementsFeed($receiver: ID!) {\n"
                "  devicesMeasurementsUpdateFeed(receiver: $receiver) {\n"
                "    receiver\n"
                "    item {\n"
                "      deviceId\n"
                "      subId\n"
                "      timestamp\n"
                "      sessionId\n"
                "      type\n"
                "      data\n"
                "    }\n"
                "  }\n"
                "}\n"
            )
        else:
            query_str = (
                "subscription DeviceStateUpdates($receiver: ID!) {\n"
                "  devicesStatesUpdateFeed(receiver: $receiver) {\n"
                "    receiver\n"
                "    item {\n"
                "      deviceId\n"
                "      desired\n"
                "      reported\n"
                "      timestamp\n"
                "      connectionState {\n"
                "        connected\n"
                "        updatedTimestamp\n"
                "      }\n"
                "    }\n"
                "  }\n"
                "}\n"
            )

        _LOGGER.debug("GraphQL subscription query for %s:\n%s", self._endpoint, query_str)

        payload = {
            "id": self._subscription_id,
            "payload": {
                "data": json.dumps(
                    {
                        "query": query_str,
                        "variables": {"receiver": self._receiver},
                    }
                ),
                "extensions": {
                    "authorization": {
                        "Authorization": f"Bearer {id_token}",
                        "host": host,
                    }
                },
            },
            "type": "start",
        }
        _LOGGER.debug("Sending subscription start for %s with receiver=%s subscription_id=%s", self._endpoint, self._receiver, self._subscription_id)
        await websocket.send(json.dumps(payload))
