"""API client for documented Harvia REST/GraphQL endpoints."""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any
from urllib.parse import quote, urlencode

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_base import HarviaApiClientBase
from .errors import HarviaAuthError, HarviaConnectionError

_LOGGER = logging.getLogger(__name__)

HARVIA_ENDPOINTS_URL = "https://api.harvia.io/endpoints"


class HarviaIoApiClient(HarviaApiClientBase):
    """Client for Harvia documented REST/GraphQL API."""

    supports_push_updates = True

    def __init__(self, hass: HomeAssistant, username: str, password: str) -> None:
        """Initialize the API client."""
        self._hass = hass
        self._username = username
        self._password = password
        self._endpoints: dict[str, Any] | None = None
        self._token_data: dict[str, Any] | None = None
        self._token_expires_at: float = 0.0
        self._user_data: dict[str, Any] | None = None
        self._ws_manager = None

    async def async_authenticate(self) -> bool:
        """Authenticate using REST token endpoint."""
        await self._async_fetch_endpoints()
        await self._async_get_valid_id_token()
        return True

    async def async_get_user_data(self) -> dict:
        """Get user metadata from ID token claims."""
        if self._user_data is not None:
            return self._user_data

        id_token = await self._async_get_valid_id_token()
        claims = _decode_jwt_payload(id_token)
        self._user_data = {
            "email": claims.get("email") or self._username,
            "organizationId": claims.get("custom:organizationId", ""),
            "username": claims.get("cognito:username") or claims.get("username", ""),
        }
        return self._user_data

    async def async_get_id_token(self) -> str:
        """Return a valid ID token."""
        return await self._async_get_valid_id_token()

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """List devices from REST API."""
        devices: list[dict[str, Any]] = []
        next_token: str | None = None

        while True:
            params: dict[str, Any] = {"maxResults": 100}
            if next_token:
                params["nextToken"] = next_token
            data = await self._async_rest_request("device", "GET", "/devices", params=params)
            device_items = _extract_device_items(data)
            for item in device_items:
                device_id = _extract_device_id(item)
                if device_id:
                    devices.append({"device_id": device_id, "raw": item})
            next_token = data.get("nextToken")
            if not next_token:
                break

        if devices:
            return devices

        # Fallback: query GraphQL device list if REST payload shape differs.
        try:
            gql_data = await self._async_graphql_request(
                "device",
                (
                    "query ListMyDevices {\n"
                    "  devicesMeList(maxResults: 100) {\n"
                    "    devices {\n"
                    "      deviceId\n"
                    "      type\n"
                    "      via\n"
                    "    }\n"
                    "    nextToken\n"
                    "  }\n"
                    "}\n"
                ),
            )
            gql_devices = (
                gql_data.get("data", {})
                .get("devicesMeList", {})
                .get("devices", [])
            )
            for item in gql_devices:
                device_id = _extract_device_id(item)
                if device_id:
                    devices.append({"device_id": device_id, "raw": item})
        except HarviaAuthError as err:
            _LOGGER.debug("GraphQL device fallback unauthorized: %s", err)
        except HarviaConnectionError as err:
            _LOGGER.debug("GraphQL device fallback failed: %s", err)

        return devices

    async def async_get_device_state(self, device_id: str) -> dict:
        """Get normalized device state."""
        data = await self._async_rest_request(
            "device",
            "GET",
            "/devices/state",
            params={"deviceId": device_id, "subId": "C1"},
        )
        return _normalize_state_payload(device_id, data)

    async def async_get_latest_device_data(self, device_id: str) -> dict:
        """Get normalized latest telemetry for a device."""
        data = await self._async_rest_request(
            "data",
            "GET",
            "/data/latest-data",
            params={"deviceId": device_id, "cabinId": "C1"},
        )
        return _normalize_telemetry_payload(data)

    async def async_request_state_change(
        self, device_id: str, payload: dict
    ) -> dict:
        """Map normalized state payload to Harvia REST commands."""
        results: list[dict[str, Any]] = []

        # Command mappings for switch-like controls.
        command_keys = [
            ("active", "SAUNA"),
            ("light", "LIGHTS"),
            ("fan", "FAN"),
            ("steamEn", "STEAMER"),
            ("steamOn", "STEAMER"),
        ]
        for key, cmd in command_keys:
            if key not in payload:
                continue
            value = payload[key]
            state = "on" if bool(value) else "off"
            res = await self._async_rest_request(
                "device",
                "POST",
                "/devices/command",
                json_data={
                    "deviceId": device_id,
                    "cabin": {"id": "C1"},
                    "command": {"type": cmd, "state": state},
                },
            )
            results.append(res)

        # Target updates are handled by dedicated endpoint.
        target_patch: dict[str, Any] = {"deviceId": device_id, "cabin": {"id": "C1"}}
        if "targetTemp" in payload:
            target_patch["temperature"] = payload["targetTemp"]
        if "targetRh" in payload:
            target_patch["humidity"] = payload["targetRh"]
        if len(target_patch) > 2:
            res = await self._async_rest_request(
                "device", "PATCH", "/devices/target", json_data=target_patch
            )
            results.append(res)

        # Best-effort duration update if API supports it in command endpoint.
        if "onTime" in payload:
            res = await self._async_rest_request(
                "device",
                "POST",
                "/devices/command",
                json_data={
                    "deviceId": device_id,
                    "cabin": {"id": "C1"},
                    "command": {"type": "ADJUST_DURATION", "state": int(payload["onTime"])},
                },
            )
            results.append(res)

        if not results:
            _LOGGER.warning("No supported command mapping for payload keys: %s", list(payload))
            return {"handled": False, "reason": "unsupported_payload"}

        return {"handled": True, "results": results}

    async def async_start_push_updates(self, on_device_update) -> None:
        """Start GraphQL websocket subscriptions for realtime updates."""
        if self._ws_manager is not None:
            return
        from .websocket_harviaio import HarviaIoWebSocketManager

        self._ws_manager = HarviaIoWebSocketManager(
            api=self,
            on_device_update=on_device_update,
        )
        await self._ws_manager.async_start()

    async def async_stop_push_updates(self) -> None:
        """Stop realtime subscriptions."""
        if self._ws_manager is None:
            return
        await self._ws_manager.async_stop()
        self._ws_manager = None

    @property
    def push_connected(self) -> bool:
        """Return True if any subscription websocket is connected."""
        if not self._ws_manager:
            return False
        return any(ws._websocket is not None for ws in self._ws_manager._connections)

    @property
    def push_connections_info(self) -> list[dict[str, Any]]:
        """Return diagnostics for subscription websocket connections."""
        if not self._ws_manager:
            return []
        return [
            {
                "label": ws._label,
                "connected": ws._websocket is not None,
                "reconnect_attempts": ws._reconnect_attempts,
            }
            for ws in self._ws_manager._connections
        ]

    async def _async_fetch_endpoints(self) -> dict[str, Any]:
        """Fetch and cache endpoints response."""
        if self._endpoints is not None:
            return self._endpoints

        session = async_get_clientsession(self._hass)
        try:
            async with session.get(HARVIA_ENDPOINTS_URL) as response:
                if response.status >= 400:
                    raise HarviaConnectionError(
                        f"Endpoints discovery failed: HTTP {response.status}"
                    )
                payload = await response.json()
        except HarviaConnectionError:
            raise
        except Exception as err:
            raise HarviaConnectionError(f"Endpoints discovery failed: {err}") from err

        self._endpoints = payload.get("endpoints", {})
        return self._endpoints

    async def async_get_websocket_info(self, service: str) -> dict[str, str]:
        """Get websocket URL and host for a GraphQL service endpoint."""
        endpoints = await self._async_fetch_endpoints()
        graphql_url = endpoints.get("GraphQL", {}).get(service, {}).get("https")
        if not graphql_url:
            raise HarviaConnectionError(f"Missing endpoints.GraphQL.{service}.https")
        if not graphql_url.endswith("/graphql"):
            raise HarviaConnectionError(
                f"Unexpected GraphQL endpoint format for {service}: {graphql_url}"
            )
        wss_url = graphql_url.replace("https://", "wss://").replace(
            "appsync-api", "appsync-realtime-api"
        )
        host = graphql_url.replace("https://", "").replace("/graphql", "")
        return {"wss_url": wss_url, "host": host}

    async def async_get_websocket_url(self, service: str) -> str:
        """Build authenticated websocket URL for GraphQL subscriptions."""
        ws_info = await self.async_get_websocket_info(service)
        id_token = await self._async_get_valid_id_token()
        header_payload = {
            "Authorization": f"Bearer {id_token}",
            "host": ws_info["host"],
        }
        encoded_header = base64.b64encode(json.dumps(header_payload).encode()).decode()
        return f"{ws_info['wss_url']}?header={quote(encoded_header)}&payload=e30="

    async def async_get_receiver_id(self) -> str:
        """Return receiver ID used in GraphQL feed subscriptions."""
        user_data = await self.async_get_user_data()
        receiver = user_data.get("username")
        if not receiver:
            raise HarviaAuthError("Missing cognito:username in id token")
        return receiver

    async def _async_get_valid_id_token(self) -> str:
        """Return valid ID token, refreshing when required."""
        if (
            self._token_data is not None
            and self._token_data.get("idToken")
            and time.time() < self._token_expires_at
        ):
            return self._token_data["idToken"]

        if self._token_data and self._token_data.get("refreshToken"):
            try:
                await self._async_refresh_tokens()
                return self._token_data["idToken"]
            except Exception as err:
                _LOGGER.debug("Token refresh failed, falling back to full login: %s", err)
                self._token_data = None

        await self._async_login()
        if not self._token_data or "idToken" not in self._token_data:
            raise HarviaAuthError("Authentication did not return idToken")
        return self._token_data["idToken"]

    async def _async_login(self) -> None:
        """Perform username/password login."""
        endpoints = await self._async_fetch_endpoints()
        base_url = endpoints.get("RestApi", {}).get("generics", {}).get("https")
        if not base_url:
            raise HarviaConnectionError("Missing endpoints.RestApi.generics.https")

        data = await self._async_raw_request(
            "POST",
            f"{base_url}/auth/token",
            {"username": self._username, "password": self._password},
            include_auth=False,
        )
        self._set_token_data(data)

    async def _async_refresh_tokens(self) -> None:
        """Refresh tokens using refresh token."""
        endpoints = await self._async_fetch_endpoints()
        base_url = endpoints.get("RestApi", {}).get("generics", {}).get("https")
        if not base_url:
            raise HarviaConnectionError("Missing endpoints.RestApi.generics.https")
        if not self._token_data or not self._token_data.get("refreshToken"):
            raise HarviaAuthError("Missing refresh token")

        data = await self._async_raw_request(
            "POST",
            f"{base_url}/auth/refresh",
            {"refreshToken": self._token_data["refreshToken"], "email": self._username},
            include_auth=False,
        )
        if "refreshToken" not in data and self._token_data.get("refreshToken"):
            data["refreshToken"] = self._token_data["refreshToken"]
        self._set_token_data(data)

    def _set_token_data(self, data: dict[str, Any]) -> None:
        """Store token data and expiration."""
        self._token_data = data
        expires_in = int(data.get("expiresIn", 3600))
        self._token_expires_at = time.time() + max(expires_in - 60, 60)

    async def _async_rest_request(
        self,
        service: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Request against a service-specific REST API base URL."""
        endpoints = await self._async_fetch_endpoints()
        base_url = endpoints.get("RestApi", {}).get(service, {}).get("https")
        if not base_url:
            raise HarviaConnectionError(f"Missing endpoints.RestApi.{service}.https")

        url = f"{base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return await self._async_raw_request(method, url, json_data)

    async def _async_raw_request(
        self,
        method: str,
        url: str,
        json_data: dict[str, Any] | None = None,
        include_auth: bool = True,
    ) -> dict[str, Any]:
        """Perform HTTP request and return parsed JSON."""
        session = async_get_clientsession(self._hass)
        headers: dict[str, str] = {}
        if include_auth:
            id_token = await self._async_get_valid_id_token()
            headers["Authorization"] = f"Bearer {id_token}"
        if json_data is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with session.request(
                method, url, json=json_data, headers=headers
            ) as response:
                body_text = await response.text()
                if response.status in (401, 403):
                    self._token_data = None
                    raise HarviaAuthError(f"HTTP {response.status}")
                if response.status >= 400:
                    raise HarviaConnectionError(
                        f"HTTP {response.status}: {body_text[:300]}"
                    )
                if not body_text:
                    return {}
                return json.loads(body_text)
        except (HarviaAuthError, HarviaConnectionError):
            raise
        except Exception as err:
            raise HarviaConnectionError(f"HTTP request failed: {err}") from err

    async def _async_graphql_request(
        self, service: str, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run a GraphQL request against a service endpoint."""
        endpoints = await self._async_fetch_endpoints()
        graphql_url = endpoints.get("GraphQL", {}).get(service, {}).get("https")
        if not graphql_url:
            raise HarviaConnectionError(f"Missing endpoints.GraphQL.{service}.https")

        id_token = await self._async_get_valid_id_token()
        session = async_get_clientsession(self._hass)
        headers = {
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/json",
        }
        payload = {"query": query, "variables": variables or {}}

        try:
            async with session.post(graphql_url, json=payload, headers=headers) as response:
                body_text = await response.text()
                if response.status in (401, 403):
                    self._token_data = None
                    raise HarviaAuthError(f"GraphQL HTTP {response.status}")
                if response.status >= 400:
                    raise HarviaConnectionError(
                        f"GraphQL HTTP {response.status}: {body_text[:300]}"
                    )
                data = json.loads(body_text) if body_text else {}
        except (HarviaAuthError, HarviaConnectionError):
            raise
        except Exception as err:
            raise HarviaConnectionError(f"GraphQL request failed: {err}") from err

        if data.get("errors"):
            raise HarviaConnectionError(f"GraphQL errors: {data['errors']}")
        return data


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without signature verification."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding).decode()
        return json.loads(decoded)
    except Exception:
        return {}


def _normalize_state_payload(device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize state payload to legacy coordinator shape."""
    state = payload.get("state", {}) if isinstance(payload.get("state"), dict) else payload

    normalized: dict[str, Any] = {"deviceId": device_id}
    key_map = {
        "displayName": "displayName",
        "active": "active",
        "light": "light",
        "lights": "light",
        "fan": "fan",
        "steamEn": "steamEn",
        "targetTemp": "targetTemp",
        "targetRh": "targetRh",
        "targetHum": "targetRh",
        "onTime": "onTime",
        "tempUnit": "tempUnit",
        "aromaEn": "aromaEn",
        "aromaLevel": "aromaLevel",
        "statusCodes": "statusCodes",
        "fwVersion": "fwVersion",
        "swVersion": "swVersion",
    }
    for source, target in key_map.items():
        if source in state:
            normalized[target] = state[source]
    return normalized


def _normalize_telemetry_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize telemetry payload to legacy coordinator shape."""
    data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    normalized: dict[str, Any] = {}

    telemetry_map = {
        "temperature": "temperature",
        "humidity": "humidity",
        "heatOn": "heatOn",
        "steamOn": "steamOn",
        "remainingTime": "remainingTime",
        "targetTemp": "targetTemp",
        "wifiRSSI": "wifiRSSI",
    }
    for source, target in telemetry_map.items():
        if source in data:
            normalized[target] = data[source]

    if "timestamp" in payload:
        normalized["timestamp"] = payload["timestamp"]
    if "type" in payload:
        normalized["type"] = payload["type"]

    return normalized


def _extract_device_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract device objects from known and variant payload shapes."""
    if isinstance(payload.get("devices"), list):
        return [item for item in payload["devices"] if isinstance(item, dict)]
    if isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    if isinstance(payload.get("results"), list):
        return [item for item in payload["results"] if isinstance(item, dict)]

    # Deep scan fallback for arrays containing dicts with a deviceId.
    found: list[dict[str, Any]] = []

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("deviceId"), str):
                found.append(value)
            for nested in value.values():
                _walk(nested)
        elif isinstance(value, list):
            for nested in value:
                _walk(nested)

    _walk(payload)
    return found


def _extract_device_id(item: dict[str, Any]) -> str | None:
    """Extract device identifier from known item shapes."""
    for key in ("deviceId", "id", "thingName", "name"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    if isinstance(item.get("i"), dict):
        name = item["i"].get("name")
        if isinstance(name, str) and name:
            return name
    return None
