"""API Client for the MyHarvia Cloud API."""

from __future__ import annotations

import base64
import json
import logging
import re
from urllib.parse import quote

import botocore.exceptions
from pycognito import Cognito

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import ENDPOINTS, MYHARVIA_BASE_URL, MYHARVIA_REGION

_LOGGER = logging.getLogger(__name__)


class HarviaAuthError(Exception):
    """Authentication failed."""


class HarviaConnectionError(Exception):
    """Connection to API failed."""


class HarviaApiClient:
    """Client for the MyHarvia Cloud API (Cognito + AppSync GraphQL)."""

    def __init__(self, hass: HomeAssistant, username: str, password: str) -> None:
        """Initialize the API client."""
        self._hass = hass
        self._username = username
        self._password = password
        self._endpoints: dict | None = None
        self._cognito: Cognito | None = None
        self._token_data: dict | None = None
        self._user_data: dict | None = None

    async def async_authenticate(self) -> bool:
        """Authenticate with MyHarvia via AWS Cognito."""
        if self._token_data is not None:
            return True

        client = await self._async_get_cognito_client()

        try:
            await self._hass.async_add_executor_job(
                client.authenticate, self._password
            )
        except botocore.exceptions.ClientError as err:
            _LOGGER.error("MyHarvia authentication failed: %s", err)
            raise HarviaAuthError(f"Authentication failed: {err}") from err
        except Exception as err:
            _LOGGER.error("MyHarvia connection error: %s", err)
            raise HarviaConnectionError(f"Connection error: {err}") from err

        self._token_data = {
            "access_token": client.access_token,
            "refresh_token": client.refresh_token,
            "id_token": client.id_token,
        }

        _LOGGER.debug("MyHarvia authentication successful")
        return True

    async def async_check_and_renew_tokens(self) -> None:
        """Check and renew tokens if needed."""
        client = await self._async_get_cognito_client()
        await self.async_authenticate()

        await self._hass.async_add_executor_job(
            lambda: client.check_token(renew=True)
        )

        self._token_data = {
            "access_token": client.access_token,
            "refresh_token": client.refresh_token,
            "id_token": client.id_token,
        }

    async def async_get_id_token(self) -> str:
        """Get a valid ID token, renewing if necessary."""
        await self.async_check_and_renew_tokens()
        return self._token_data["id_token"]

    async def async_get_endpoints(self) -> dict:
        """Fetch API endpoints from MyHarvia cloud."""
        if self._endpoints is not None:
            return self._endpoints

        self._endpoints = {}
        session = async_get_clientsession(self._hass)

        for endpoint in ENDPOINTS:
            url = f"{MYHARVIA_BASE_URL}/{endpoint}/endpoint"
            try:
                async with session.get(url) as response:
                    self._endpoints[endpoint] = await response.json()
            except Exception as err:
                _LOGGER.error("Failed to fetch endpoint %s: %s", endpoint, err)
                raise HarviaConnectionError(
                    f"Failed to fetch endpoint {endpoint}: {err}"
                ) from err

        _LOGGER.debug("MyHarvia endpoints fetched successfully")
        return self._endpoints

    async def async_graphql_request(
        self, endpoint: str, query: dict
    ) -> dict:
        """Execute a GraphQL request against the MyHarvia API."""
        id_token = await self.async_get_id_token()
        headers = {"authorization": id_token}
        session = async_get_clientsession(self._hass)
        endpoints = await self.async_get_endpoints()
        url = endpoints[endpoint]["endpoint"]

        async with session.post(url, json=query, headers=headers) as response:
            data = await response.json()
            return data

    async def async_get_user_data(self) -> dict:
        """Get current user details."""
        if self._user_data is not None:
            return self._user_data

        query = {
            "operationName": "Query",
            "variables": {},
            "query": (
                "query Query {\n"
                "  getCurrentUserDetails {\n"
                "    email\n"
                "    organizationId\n"
                "    admin\n"
                "    given_name\n"
                "    family_name\n"
                "    superAdmin\n"
                "    rdUser\n"
                "    appSettings\n"
                "    __typename\n"
                "  }\n"
                "}\n"
            ),
        }
        data = await self.async_graphql_request("users", query)
        self._user_data = data["data"]["getCurrentUserDetails"]
        return self._user_data

    async def async_get_device_tree(self) -> list[dict]:
        """Get all devices from the device tree."""
        query = {
            "operationName": "Query",
            "variables": {},
            "query": "query Query {\n  getDeviceTree\n}\n",
        }
        result = await self.async_graphql_request("device", query)

        if "data" not in result or "getDeviceTree" not in result["data"]:
            _LOGGER.error("Unexpected device tree response structure")
            return []

        tree_data = json.loads(result["data"]["getDeviceTree"])
        if not tree_data:
            _LOGGER.warning("No devices found in device tree")
            return []

        return tree_data[0].get("c", [])

    async def async_get_device_state(self, device_id: str) -> dict:
        """Get current device state (reported)."""
        query = {
            "operationName": "Query",
            "variables": {"deviceId": device_id},
            "query": (
                "query Query($deviceId: ID!) {\n"
                "  getDeviceState(deviceId: $deviceId) {\n"
                "    desired\n"
                "    reported\n"
                "    timestamp\n"
                "    __typename\n"
                "  }\n"
                "}\n"
            ),
        }
        data = await self.async_graphql_request("device", query)
        return json.loads(data["data"]["getDeviceState"]["reported"])

    async def async_get_latest_device_data(self, device_id: str) -> dict:
        """Get latest telemetry data for a device."""
        query = {
            "operationName": "Query",
            "variables": {"deviceId": device_id},
            "query": (
                "query Query($deviceId: String!) {\n"
                "  getLatestData(deviceId: $deviceId) {\n"
                "    deviceId\n"
                "    timestamp\n"
                "    sessionId\n"
                "    type\n"
                "    data\n"
                "    __typename\n"
                "  }\n"
                "}\n"
            ),
        }
        data = await self.async_graphql_request("data", query)
        latest = data["data"]["getLatestData"]
        device_data = json.loads(latest["data"])
        device_data["timestamp"] = latest["timestamp"]
        device_data["type"] = latest["type"]
        return device_data

    async def async_request_state_change(
        self, device_id: str, payload: dict
    ) -> dict:
        """Send a state change mutation to the device."""
        payload_string = json.dumps(payload)
        query = {
            "operationName": "Mutation",
            "variables": {
                "deviceId": device_id,
                "state": payload_string,
                "getFullState": False,
            },
            "query": (
                "mutation Mutation("
                "$deviceId: ID!, $state: AWSJSON!, $getFullState: Boolean"
                ") {\n"
                "  requestStateChange("
                "deviceId: $deviceId, state: $state, getFullState: $getFullState"
                ")\n"
                "}\n"
            ),
        }
        return await self.async_graphql_request("device", query)

    async def async_get_websocket_info(self, endpoint: str) -> dict:
        """Get WebSocket connection URL and host for an endpoint."""
        endpoints = await self.async_get_endpoints()
        endpoint_url = endpoints[endpoint]["endpoint"]
        regex = r"^https://(.+)\.appsync-api\.(.+)/graphql$"
        wss_url = re.sub(regex, r"wss://\1.appsync-realtime-api.\2/graphql", endpoint_url)
        host = re.sub(regex, r"\1.appsync-api.\2", endpoint_url)
        return {"wss_url": wss_url, "host": host}

    async def async_get_websocket_url(self, endpoint: str) -> str:
        """Build the full authenticated WebSocket URL."""
        ws_info = await self.async_get_websocket_info(endpoint)
        id_token = await self.async_get_id_token()
        header_payload = {
            "Authorization": id_token,
            "host": ws_info["host"],
        }
        encoded_header = base64.b64encode(
            json.dumps(header_payload).encode()
        ).decode()
        return (
            f"{ws_info['wss_url']}"
            f"?header={quote(encoded_header)}"
            f"&payload=e30="
        )

    # -- Private helpers --

    async def _async_get_cognito_client(self) -> Cognito:
        """Get or create the Cognito client."""
        if self._cognito is not None:
            return self._cognito

        endpoints = await self.async_get_endpoints()
        user_pool_id = endpoints["users"]["userPoolId"]
        client_id = endpoints["users"]["clientId"]

        self._cognito = await self._hass.async_add_executor_job(
            Cognito,
            user_pool_id,
            client_id,
            None,  # user_pool_region is keyword arg
        )
        # Set attributes that couldn't be passed as kwargs in positional call
        self._cognito.username = self._username
        self._cognito.user_pool_region = MYHARVIA_REGION

        return self._cognito
