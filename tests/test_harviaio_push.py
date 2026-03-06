"""Unit tests for Harvia API push mapping and lifecycle."""

from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import patch


# Minimal third-party stubs used by package imports.
if "botocore" not in sys.modules:
    botocore_mod = types.ModuleType("botocore")
    botocore_exc = types.ModuleType("botocore.exceptions")

    class ClientError(Exception):  # pragma: no cover - test stub
        pass

    botocore_exc.ClientError = ClientError
    sys.modules["botocore"] = botocore_mod
    sys.modules["botocore.exceptions"] = botocore_exc

if "pycognito" not in sys.modules:
    pycognito_mod = types.ModuleType("pycognito")

    class Cognito:  # pragma: no cover - test stub
        def __init__(self, *_args, **_kwargs):
            pass

    pycognito_mod.Cognito = Cognito
    sys.modules["pycognito"] = pycognito_mod

if "websockets" not in sys.modules:
    ws_mod = types.ModuleType("websockets")
    ws_exc = types.ModuleType("websockets.exceptions")

    class ConnectionClosedError(Exception):  # pragma: no cover - test stub
        pass

    async def connect(*_args, **_kwargs):  # pragma: no cover - test stub
        raise RuntimeError("websocket connect not available in unit tests")

    ws_exc.ConnectionClosedError = ConnectionClosedError
    ws_mod.exceptions = ws_exc
    ws_mod.connect = connect
    sys.modules["websockets"] = ws_mod
    sys.modules["websockets.exceptions"] = ws_exc


# Minimal voluptuous stub required by package __init__ import side effects.
if "voluptuous" not in sys.modules:
    vol = types.ModuleType("voluptuous")

    def _identity(*args, **kwargs):
        return lambda value=None: value

    class _Schema:
        def __init__(self, *_args, **_kwargs):
            pass

        def __call__(self, value):
            return value

    vol.Schema = _Schema
    vol.Required = lambda key, **kwargs: key
    vol.Optional = lambda key, **kwargs: key
    vol.All = _identity
    vol.Coerce = lambda _t: (lambda value: value)
    vol.Range = _identity
    sys.modules["voluptuous"] = vol


# Minimal Home Assistant stubs so module imports work in plain unit tests.
if "homeassistant" not in sys.modules:
    ha_mod = types.ModuleType("homeassistant")
    ha_config_entries = types.ModuleType("homeassistant.config_entries")
    ha_const = types.ModuleType("homeassistant.const")
    ha_core = types.ModuleType("homeassistant.core")
    ha_exceptions = types.ModuleType("homeassistant.exceptions")
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_cv = types.ModuleType("homeassistant.helpers.config_validation")
    ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
    ha_uc = types.ModuleType("homeassistant.helpers.update_coordinator")

    class HomeAssistant:  # pragma: no cover - test stub
        pass

    class ConfigEntry:  # pragma: no cover - test stub
        pass

    class ServiceCall:  # pragma: no cover - test stub
        pass

    class ConfigEntryAuthFailed(Exception):  # pragma: no cover - test stub
        pass

    class ConfigEntryNotReady(Exception):  # pragma: no cover - test stub
        pass

    class UpdateFailed(Exception):  # pragma: no cover - test stub
        pass

    class DataUpdateCoordinator:  # pragma: no cover - test stub
        def __init__(self, hass, logger, name, config_entry=None, update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.config_entry = config_entry
            self.update_interval = update_interval
            self.data = None
            self.last_update_success = True

        def __class_getitem__(cls, _item):
            return cls

        def async_set_updated_data(self, data):
            self.data = data

    async def async_get_clientsession(_hass):  # pragma: no cover - test stub
        raise RuntimeError("network session not available in unit tests")

    ha_config_entries.ConfigEntry = ConfigEntry
    ha_const.CONF_USERNAME = "username"
    ha_const.CONF_PASSWORD = "password"
    ha_const.Platform = types.SimpleNamespace(
        BINARY_SENSOR="binary_sensor",
        CLIMATE="climate",
        NUMBER="number",
        SENSOR="sensor",
        SWITCH="switch",
    )
    ha_core.HomeAssistant = HomeAssistant
    ha_core.ServiceCall = ServiceCall
    ha_exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    ha_exceptions.ConfigEntryNotReady = ConfigEntryNotReady
    ha_cv.string = str
    ha_cv.boolean = bool
    ha_aiohttp.async_get_clientsession = async_get_clientsession
    ha_uc.DataUpdateCoordinator = DataUpdateCoordinator
    ha_uc.UpdateFailed = UpdateFailed

    sys.modules["homeassistant"] = ha_mod
    sys.modules["homeassistant.config_entries"] = ha_config_entries
    sys.modules["homeassistant.const"] = ha_const
    sys.modules["homeassistant.core"] = ha_core
    sys.modules["homeassistant.exceptions"] = ha_exceptions
    sys.modules["homeassistant.helpers"] = ha_helpers
    sys.modules["homeassistant.helpers.config_validation"] = ha_cv
    sys.modules["homeassistant.helpers.aiohttp_client"] = ha_aiohttp
    sys.modules["homeassistant.helpers.update_coordinator"] = ha_uc

from custom_components.harvia_sauna.api_harviaio import (  # noqa: E402
    HarviaIoApiClient,
    _extract_device_id,
    _extract_device_items,
    _normalize_state_payload,
    _normalize_telemetry_payload,
)
from custom_components.harvia_sauna.websocket_harviaio import (  # noqa: E402
    HarviaIoWebSocketManager,
)
from custom_components.harvia_sauna.const import SCAN_INTERVAL_FALLBACK  # noqa: E402
from custom_components.harvia_sauna.coordinator import (  # noqa: E402
    DEVICE_STALE_TIMEOUT,
    HarviaDeviceData,
    HarviaSaunaCoordinator,
    HarviaSaunaData,
)


class TestHarviaIoNormalization(unittest.TestCase):
    """Test payload normalization helpers."""

    def test_normalize_state_payload_maps_target_humidity(self) -> None:
        payload = {
            "state": {
                "displayName": "Sauna One",
                "active": 1,
                "lights": True,
                "fan": False,
                "targetTemp": 82,
                "targetHum": 45,
                "statusCodes": "190",
            }
        }

        result = _normalize_state_payload("DEVICE-1", payload)

        self.assertEqual(result["deviceId"], "DEVICE-1")
        self.assertEqual(result["displayName"], "Sauna One")
        self.assertEqual(result["targetTemp"], 82)
        self.assertEqual(result["targetRh"], 45)
        self.assertTrue(result["light"])

    def test_normalize_telemetry_payload_maps_known_fields(self) -> None:
        payload = {
            "timestamp": "2025-01-01T00:00:00.000Z",
            "type": "HEATER",
            "data": {
                "temperature": 76.1,
                "humidity": 33,
                "heatOn": True,
                "steamOn": False,
                "remainingTime": 57,
                "wifiRSSI": -62,
            },
        }

        result = _normalize_telemetry_payload(payload)

        self.assertEqual(result["temperature"], 76.1)
        self.assertEqual(result["remainingTime"], 57)
        self.assertEqual(result["wifiRSSI"], -62)
        self.assertEqual(result["timestamp"], "2025-01-01T00:00:00.000Z")

    def test_extract_device_items_supports_variant_shapes(self) -> None:
        payload = {"items": [{"deviceId": "D1"}, {"deviceId": "D2"}]}
        result = _extract_device_items(payload)
        self.assertEqual([item["deviceId"] for item in result], ["D1", "D2"])

    def test_extract_device_id_supports_multiple_keys(self) -> None:
        self.assertEqual(_extract_device_id({"deviceId": "D1"}), "D1")
        self.assertEqual(_extract_device_id({"id": "D2"}), "D2")
        self.assertEqual(_extract_device_id({"thingName": "D3"}), "D3")
        self.assertEqual(_extract_device_id({"i": {"name": "D4"}}), "D4")


class TestHarviaIoPushMapping(unittest.IsolatedAsyncioTestCase):
    """Test subscription message mapping for coordinator compatibility."""

    async def test_device_feed_maps_to_on_state_updated(self) -> None:
        received: list[dict] = []

        async def _callback(payload: dict) -> None:
            received.append(payload)

        manager = HarviaIoWebSocketManager(api=object(), on_device_update=_callback)

        await manager._handle_message(
            "device",
            {
                "payload": {
                    "data": {
                        "devicesStatesUpdateFeed": {
                            "item": {
                                "reported": {"deviceId": "DEV-1", "active": 1}
                            }
                        }
                    }
                }
            },
        )

        self.assertEqual(len(received), 1)
        wrapped = received[0]
        self.assertIn("onStateUpdated", wrapped)
        reported = wrapped["onStateUpdated"]["reported"]
        self.assertIsInstance(reported, str)
        self.assertEqual(json.loads(reported)["deviceId"], "DEV-1")

    async def test_data_feed_maps_to_on_data_updates(self) -> None:
        received: list[dict] = []

        async def _callback(payload: dict) -> None:
            received.append(payload)

        manager = HarviaIoWebSocketManager(api=object(), on_device_update=_callback)

        await manager._handle_message(
            "data",
            {
                "payload": {
                    "data": {
                        "devicesMeasurementsUpdateFeed": {
                            "item": {
                                "deviceId": "DEV-2",
                                "timestamp": "1700000000000",
                                "data": {"temperature": 81},
                            }
                        }
                    }
                }
            },
        )

        self.assertEqual(len(received), 1)
        wrapped = received[0]
        self.assertIn("onDataUpdates", wrapped)
        item = wrapped["onDataUpdates"]["item"]
        self.assertEqual(item["deviceId"], "DEV-2")
        self.assertEqual(json.loads(item["data"])["temperature"], 81)


class TestHarviaIoPushLifecycle(unittest.IsolatedAsyncioTestCase):
    """Test API client push lifecycle methods."""

    async def test_start_and_stop_push_updates(self) -> None:
        class FakeManager:
            def __init__(self, api, on_device_update):
                self.api = api
                self.on_device_update = on_device_update
                self._connections = []
                self.started = False
                self.stopped = False

            async def async_start(self):
                self.started = True

            async def async_stop(self):
                self.stopped = True

        client = HarviaIoApiClient(hass=object(), username="u", password="p")

        async def _noop(_payload):
            return None

        with patch(
            "custom_components.harvia_sauna.websocket_harviaio.HarviaIoWebSocketManager",
            FakeManager,
        ):
            await client.async_start_push_updates(_noop)
            self.assertIsNotNone(client._ws_manager)
            self.assertTrue(client._ws_manager.started)

            await client.async_stop_push_updates()
            self.assertIsNone(client._ws_manager)


class TestHarviaIoCommandMapping(unittest.IsolatedAsyncioTestCase):
    """Test command mapping edge cases for harvia.io provider."""

    async def test_command_mapping_builds_expected_requests(self) -> None:
        client = HarviaIoApiClient(hass=object(), username="u", password="p")
        calls: list[tuple[str, str, str, dict | None]] = []

        async def _fake_rest(service, method, path, params=None, json_data=None):
            calls.append((service, method, path, json_data))
            return {"ok": True}

        client._async_rest_request = _fake_rest  # type: ignore[method-assign]

        result = await client.async_request_state_change(
            "DEV-1",
            {
                "active": 1,
                "light": 0,
                "targetTemp": 85,
                "targetRh": 40,
                "onTime": 50,
            },
        )

        self.assertTrue(result["handled"])
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][2], "/devices/command")
        self.assertEqual(calls[0][3]["command"]["type"], "SAUNA")
        self.assertEqual(calls[0][3]["command"]["state"], "on")
        self.assertEqual(calls[1][3]["command"]["type"], "LIGHTS")
        self.assertEqual(calls[1][3]["command"]["state"], "off")
        self.assertEqual(calls[2][2], "/devices/target")
        self.assertEqual(calls[2][3]["temperature"], 85)
        self.assertEqual(calls[2][3]["humidity"], 40)
        self.assertEqual(calls[3][3]["command"]["type"], "ADJUST_DURATION")
        self.assertEqual(calls[3][3]["command"]["state"], 50)

    async def test_unsupported_payload_returns_handled_false(self) -> None:
        client = HarviaIoApiClient(hass=object(), username="u", password="p")

        async def _unexpected_rest(*_args, **_kwargs):
            raise AssertionError("REST call should not happen for unsupported payload")

        client._async_rest_request = _unexpected_rest  # type: ignore[method-assign]
        result = await client.async_request_state_change("DEV-1", {"unknownKey": 1})
        self.assertFalse(result["handled"])
        self.assertEqual(result["reason"], "unsupported_payload")


class TestCoordinatorTiming(unittest.TestCase):
    """Test coordinator refresh interval and stale checks."""

    def _make_api(self):
        class FakeApi:
            push_connected = False
            push_connections_info = []

            async def async_start_push_updates(self, _on_device_update):
                return None

            async def async_stop_push_updates(self):
                return None

            async def async_get_devices(self):
                return []

            async def async_get_device_state(self, _device_id):
                return {}

            async def async_get_latest_device_data(self, _device_id):
                return {}

            async def async_request_state_change(self, _device_id, _payload):
                return {}

        return FakeApi()

    def test_coordinator_uses_fallback_scan_interval(self) -> None:
        coordinator = HarviaSaunaCoordinator(
            hass=object(),
            api=self._make_api(),
            config_entry=object(),
        )
        self.assertIsNotNone(coordinator.update_interval)
        self.assertEqual(
            int(coordinator.update_interval.total_seconds()),
            SCAN_INTERVAL_FALLBACK,
        )

    def test_is_device_stale_threshold_behavior(self) -> None:
        coordinator = HarviaSaunaCoordinator(
            hass=object(),
            api=self._make_api(),
            config_entry=object(),
        )
        device = HarviaDeviceData(device_id="DEV-1")
        device._last_update = 0.0
        coordinator.data = HarviaSaunaData(devices={"DEV-1": device})

        # New device without timestamp is not stale.
        self.assertFalse(coordinator.is_device_stale("DEV-1"))

        # Older than stale timeout is stale.
        import time as _time

        device._last_update = _time.monotonic() - (DEVICE_STALE_TIMEOUT + 1)
        self.assertTrue(coordinator.is_device_stale("DEV-1"))


if __name__ == "__main__":
    unittest.main()
