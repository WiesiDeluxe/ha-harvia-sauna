"""WebSocket subscriptions for Harvia documented GraphQL feeds."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import uuid
from typing import Any, Awaitable, Callable

import websockets

from .api_harviaio import HarviaIoApiClient, _normalize_state_payload, _normalize_telemetry_payload
from .const import WS_HEARTBEAT_TIMEOUT, WS_MAX_RECONNECT_DELAY, WS_RECONNECT_INTERVAL

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

    async def async_start(self) -> None:
        """Start data and device feed subscriptions."""
        if self._running:
            _LOGGER.debug("WebSocket manager already running")
            return

        try:
            receiver = await self._api.async_get_receiver_id()
            _LOGGER.debug("Starting WebSocket subscriptions for receiver: %s", receiver)
        except Exception as err:
            _LOGGER.error("Failed to get receiver ID for subscriptions: %s", err)
            return

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
        for ws in self._connections:
            await ws.async_stop()
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._connections.clear()
        self._tasks.clear()
        _LOGGER.debug("WebSocket subscriptions stopped")

    async def _handle_message(self, endpoint: str, message: dict[str, Any]) -> None:
        """Map provider feed payloads into coordinator-compatible update payloads."""
        payload_data = message.get("payload", {}).get("data", {})
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
            except Exception as err:
                if not self._running:
                    break
                err_str = str(err)
                if "401" in err_str or "403" in err_str or "Unauthorized" in err_str:
                    self._reconnect_attempts = 0
                    _LOGGER.warning("Harvia feed %s authentication error: %s", self._label, err)
                else:
                    _LOGGER.warning("Harvia feed %s connection error: %s", self._label, err)

            if not self._running:
                break
            delay = min(
                2 ** self._reconnect_attempts + random.uniform(0, 1),
                WS_MAX_RECONNECT_DELAY,
            )
            self._reconnect_attempts += 1
            _LOGGER.debug(
                "Harvia feed %s reconnecting in %.1f seconds (attempt %d)",
                self._label,
                delay,
                self._reconnect_attempts,
            )
            await asyncio.sleep(delay)

    async def async_stop(self) -> None:
        """Stop websocket connection."""
        self._running = False
        if self._websocket is not None:
            try:
                await self._websocket.send(
                    json.dumps({"id": self._subscription_id, "type": "stop"})
                )
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None

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
            self._reconnect_attempts = 0

            await websocket.send(json.dumps({"type": "connection_init"}))

            timeout = WS_HEARTBEAT_TIMEOUT
            reconnect_timer = 0.0
            subscription_active = False

            while self._running:
                try:
                    raw_message = await asyncio.wait_for(
                        websocket.recv(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    break
                except websockets.exceptions.ConnectionClosedError:
                    break

                message = json.loads(raw_message)
                msg_type = message.get("type")
                _LOGGER.debug("WebSocket %s received message type=%s payload=%s", self._label, msg_type, str(message)[:200])

                if msg_type == "ka":
                    reconnect_timer += timeout
                    _LOGGER.debug("WebSocket %s keepalive, reconnect_timer=%.0f/%.0f", self._label, reconnect_timer, WS_RECONNECT_INTERVAL)
                    if reconnect_timer >= WS_RECONNECT_INTERVAL:
                        _LOGGER.debug("WebSocket %s reconnect interval exceeded, reconnecting", self._label)
                        break
                elif msg_type == "connection_ack":
                    _LOGGER.debug("WebSocket %s connection acknowledged", self._label)
                    if message.get("payload"):
                        timeout = message["payload"]["connectionTimeoutMs"] / 1000
                        _LOGGER.debug("WebSocket %s timeout set to %.0f seconds", self._label, timeout)
                    await self._async_start_subscription(websocket, ws_info["host"], id_token)
                elif msg_type == "start_ack":
                    # Subscription successfully registered, now expecting data
                    subscription_active = True
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
                    break
                elif msg_type == "error":
                    error_msg = message.get("payload", {})
                    _LOGGER.error("Feed %s received error: %s", self._label, error_msg)
                else:
                    _LOGGER.debug("WebSocket %s received unknown message type: %s full_message=%s", self._label, msg_type, message)

        self._websocket = None

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
