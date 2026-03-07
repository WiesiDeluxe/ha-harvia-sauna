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

from .api_harviaio import HarviaIoApiClient
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
            return

        receiver = await self._api.async_get_receiver_id()
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

    async def async_stop(self) -> None:
        """Stop all active websocket subscriptions."""
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

    async def _handle_message(self, endpoint: str, message: dict[str, Any]) -> None:
        """Map provider feed payloads into coordinator-compatible update payloads."""
        payload_data = message.get("payload", {}).get("data", {})
        if endpoint == "device":
            feed = payload_data.get("devicesStatesUpdateFeed", {})
            item = feed.get("item", {})
            reported = item.get("reported")
            if reported is None:
                return
            if not isinstance(reported, str):
                reported = json.dumps(reported)
            await self._on_device_update({"onStateUpdated": {"reported": reported}})
            return

        if endpoint == "data":
            feed = payload_data.get("devicesMeasurementsUpdateFeed", {})
            item = feed.get("item", {})
            data_field = item.get("data")
            if data_field is None:
                return
            if not isinstance(data_field, str):
                data_field = json.dumps(data_field)
            await self._on_device_update(
                {
                    "onDataUpdates": {
                        "item": {
                            "deviceId": item.get("deviceId"),
                            "timestamp": item.get("timestamp"),
                            "data": data_field,
                        }
                    }
                }
            )


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
                await self._async_connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as err:
                if not self._running:
                    break
                err_str = str(err)
                if "401" in err_str or "403" in err_str or "Unauthorized" in err_str:
                    self._reconnect_attempts = 0
                _LOGGER.debug("Harvia feed %s error: %s", self._label, err)

            if not self._running:
                break
            delay = min(
                2 ** self._reconnect_attempts + random.uniform(0, 1),
                WS_MAX_RECONNECT_DELAY,
            )
            self._reconnect_attempts += 1
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
        url = await self._api.async_get_websocket_url(self._endpoint)

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
                if msg_type == "ka":
                    reconnect_timer += timeout
                    if reconnect_timer >= WS_RECONNECT_INTERVAL:
                        break
                elif msg_type == "connection_ack":
                    if message.get("payload"):
                        timeout = message["payload"]["connectionTimeoutMs"] / 1000
                    await self._async_start_subscription(websocket, ws_info["host"])
                elif msg_type == "data":
                    await self._on_message(self._endpoint, message)

        self._websocket = None

    async def _async_start_subscription(self, websocket, host: str) -> None:
        """Send GraphQL subscription start frame."""
        id_token = await self._api.async_get_id_token()
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
        await websocket.send(json.dumps(payload))
