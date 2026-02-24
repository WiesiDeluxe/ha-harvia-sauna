"""DataUpdateCoordinator for Harvia Sauna."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HarviaApiClient, HarviaAuthError, HarviaConnectionError
from .const import DOMAIN, SCAN_INTERVAL_FALLBACK
from .websocket import HarviaWebSocketManager

_LOGGER = logging.getLogger(__name__)


@dataclass
class HarviaDeviceData:
    """Parsed data for a single Harvia device."""

    device_id: str = ""
    display_name: str = "Harvia Sauna"

    # State
    active: bool = False
    lights_on: bool = False
    fan_on: bool = False
    steam_on: bool = False
    steam_enabled: bool = False
    aroma_enabled: bool = False
    aroma_level: int = 0
    auto_light: bool = False
    auto_fan: bool = False
    dehumidifier_enabled: bool = False

    # Temperatures
    target_temp: int | None = None
    current_temp: int | None = None
    target_rh: int = 0
    humidity: int = 0
    temp_unit: int = 0  # 0 = Celsius

    # Timers
    heat_up_time: int = 0
    remaining_time: int = 0
    on_time: int = 360  # Default max time in minutes

    # Status
    status_codes: str | None = None
    door_open: bool = False
    heat_on: bool = False

    # Telemetry
    wifi_rssi: int | None = None
    timestamp: str | None = None

    # Relay counters (for diagnostics)
    ph1_relay_counter: int = 0
    ph2_relay_counter: int = 0
    ph3_relay_counter: int = 0
    ph1_relay_counter_lt: int = 0
    ph2_relay_counter_lt: int = 0
    ph3_relay_counter_lt: int = 0

    # Steam counters
    steam_on_counter: int = 0
    steam_on_counter_lt: int = 0
    heat_on_counter: int = 0
    heat_on_counter_lt: int = 0

    # Power / Energy (calculated)
    heater_power: int = 10800  # Nennleistung in Watt (wird aus Config überschrieben)
    energy_kwh: float = 0.0  # Kumulierter Energieverbrauch in kWh
    _last_heat_on_timestamp: float | None = None  # Für Energy-Berechnung


@dataclass
class HarviaSaunaData:
    """Container for all Harvia data."""

    devices: dict[str, HarviaDeviceData] = field(default_factory=dict)
    available: bool = True


class HarviaSaunaCoordinator(DataUpdateCoordinator[HarviaSaunaData]):
    """Coordinator for Harvia Sauna data.

    Uses WebSocket push for real-time updates with a fallback
    polling interval for resilience.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: HarviaApiClient,
        config_entry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # Fallback polling - WebSocket is primary
            update_interval=timedelta(seconds=SCAN_INTERVAL_FALLBACK),
        )
        self.api = api
        self.config_entry = config_entry
        self._ws_manager: HarviaWebSocketManager | None = None

    async def async_setup(self) -> None:
        """Set up WebSocket connections for real-time updates."""
        self._ws_manager = HarviaWebSocketManager(
            api=self.api,
            on_device_update=self._async_handle_ws_update,
        )
        await self._ws_manager.async_start()

    async def async_shutdown(self) -> None:
        """Shut down WebSocket connections."""
        if self._ws_manager:
            await self._ws_manager.async_stop()
            self._ws_manager = None

    async def _async_update_data(self) -> HarviaSaunaData:
        """Fetch data via REST API (fallback polling)."""
        try:
            device_tree = await self.api.async_get_device_tree()
            data = HarviaSaunaData()

            for device_entry in device_tree:
                device_id = device_entry["i"]["name"]

                # Fetch both state and latest telemetry
                state = await self.api.async_get_device_state(device_id)
                telemetry = await self.api.async_get_latest_device_data(device_id)

                device_data = HarviaDeviceData(device_id=device_id)
                _apply_state_data(device_data, state)
                _apply_telemetry_data(device_data, telemetry)

                data.devices[device_id] = device_data

            data.available = True
            return data

        except HarviaAuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except HarviaConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Error fetching data: {err}") from err

    async def _async_handle_ws_update(self, payload_data: dict) -> None:
        """Handle incoming WebSocket push data."""
        if not self.data:
            # No initial data yet, skip
            return

        updated = False

        if "onStateUpdated" in payload_data:
            reported = payload_data["onStateUpdated"].get("reported")
            if reported:
                state = json.loads(reported)
                device_id = state.get("deviceId")
                if device_id and device_id in self.data.devices:
                    _apply_state_data(self.data.devices[device_id], state)
                    updated = True

        elif "onDataUpdates" in payload_data:
            item = payload_data["onDataUpdates"].get("item", {})
            device_id = item.get("deviceId")
            if device_id and device_id in self.data.devices:
                telemetry = json.loads(item.get("data", "{}"))
                telemetry["timestamp"] = item.get("timestamp")
                _apply_telemetry_data(self.data.devices[device_id], telemetry)
                updated = True

        if updated:
            self.async_set_updated_data(self.data)

    async def async_request_state_change(
        self, device_id: str, payload: dict
    ) -> None:
        """Send a state change command to a device."""
        await self.api.async_request_state_change(device_id, payload)


def _apply_state_data(device: HarviaDeviceData, data: dict) -> None:
    """Apply device state (reported) data to the device object."""
    if "displayName" in data:
        device.display_name = data["displayName"]
    if "deviceId" in data:
        device.device_id = data["deviceId"]
    if "active" in data:
        device.active = bool(data["active"])
    if "light" in data:
        device.lights_on = bool(data["light"])
    if "fan" in data:
        device.fan_on = bool(data["fan"])
    if "steamEn" in data:
        device.steam_enabled = bool(data["steamEn"])
    if "targetTemp" in data:
        device.target_temp = data["targetTemp"]
    if "targetRh" in data:
        device.target_rh = data["targetRh"]
    if "heatUpTime" in data:
        device.heat_up_time = data["heatUpTime"]
    if "onTime" in data:
        device.on_time = data["onTime"]
    if "tz" in data:
        pass  # timezone info, not needed
    if "dehumEn" in data:
        device.dehumidifier_enabled = bool(data["dehumEn"])
    if "autoLight" in data:
        device.auto_light = bool(data["autoLight"])
    if "autoFan" in data:
        device.auto_fan = bool(data["autoFan"])
    if "tempUnit" in data:
        device.temp_unit = data["tempUnit"]
    if "aromaEn" in data:
        device.aroma_enabled = bool(data["aromaEn"])
    if "aromaLevel" in data:
        device.aroma_level = data["aromaLevel"]
    if "statusCodes" in data:
        device.status_codes = str(data["statusCodes"])
        # Parse door status from status codes (2nd digit = 9 means door open)
        try:
            device.door_open = int(str(data["statusCodes"])[1]) == 9
        except (IndexError, ValueError):
            pass


def _apply_telemetry_data(device: HarviaDeviceData, data: dict) -> None:
    """Apply telemetry (sensor) data to the device object."""
    if "temperature" in data:
        device.current_temp = data["temperature"]
    if "humidity" in data:
        device.humidity = data["humidity"]
    if "heatOn" in data:
        was_heating = device.heat_on
        device.heat_on = bool(data["heatOn"])

        # Energy calculation: accumulate kWh while heating
        now = time.monotonic()
        if was_heating and device._last_heat_on_timestamp is not None:
            elapsed_hours = (now - device._last_heat_on_timestamp) / 3600.0
            device.energy_kwh += (device.heater_power / 1000.0) * elapsed_hours

        if device.heat_on:
            device._last_heat_on_timestamp = now
        else:
            device._last_heat_on_timestamp = None

    if "steamOn" in data:
        device.steam_on = bool(data["steamOn"])
    if "remainingTime" in data:
        device.remaining_time = data["remainingTime"]
    if "targetTemp" in data:
        device.target_temp = data["targetTemp"]
    if "wifiRSSI" in data:
        device.wifi_rssi = data["wifiRSSI"]
    if "timestamp" in data:
        device.timestamp = data["timestamp"]

    # Relay counters
    for key, attr in [
        ("ph1RelayCounter", "ph1_relay_counter"),
        ("ph2RelayCounter", "ph2_relay_counter"),
        ("ph3RelayCounter", "ph3_relay_counter"),
        ("ph1RelayCounterLT", "ph1_relay_counter_lt"),
        ("ph2RelayCounterLT", "ph2_relay_counter_lt"),
        ("ph3RelayCounterLT", "ph3_relay_counter_lt"),
        ("steamOnCounter", "steam_on_counter"),
        ("steamOnCounterLT", "steam_on_counter_lt"),
        ("heatOnCounter", "heat_on_counter"),
        ("heatOnCounterLT", "heat_on_counter_lt"),
    ]:
        if key in data:
            setattr(device, attr, data[key])
