"""DataUpdateCoordinator for Harvia Sauna."""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_base import HarviaApiClientBase
from .const import (
    DOMAIN,
    EVENT_SESSION_END,
    EVENT_SESSION_START,
    SCAN_INTERVAL_FALLBACK,
)
from .errors import HarviaAuthError, HarviaConnectionError

_LOGGER = logging.getLogger(__name__)

# Device is considered stale if no update received for this many seconds
DEVICE_STALE_TIMEOUT = 600  # 10 minutes

# Temperature trend: keep last N readings for rate calculation
TEMP_HISTORY_MAX = 10


@dataclass
class HarviaDeviceData:
    """Parsed data for a single Harvia device."""

    device_id: str = ""
    display_name: str = "Harvia Sauna"
    firmware_version: str | None = None

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
    _last_update: float = 0.0  # monotonic timestamp of last data received

    # Session tracking
    _session_active: bool = False
    _session_start_time: float | None = None
    _session_max_temp: float = 0.0
    last_session_duration: float = 0.0  # Minuten
    last_session_max_temp: float = 0.0  # °C
    sessions_today: int = 0
    _sessions_today_date: str = ""  # ISO date string for reset

    # Temperature trend (°C/min)
    _temp_history: deque = field(
        default_factory=lambda: deque(maxlen=TEMP_HISTORY_MAX)
    )
    temp_trend: float | None = None  # °C/min


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

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: HarviaApiClientBase,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            # Fallback polling - WebSocket is primary
            update_interval=timedelta(seconds=SCAN_INTERVAL_FALLBACK),
        )
        self.api = api

    async def async_setup(self) -> None:
        """Set up WebSocket connections for real-time updates."""
        await self.api.async_start_push_updates(self._async_handle_ws_update)

    async def async_shutdown(self) -> None:
        """Shut down WebSocket connections."""
        await self.api.async_stop_push_updates()

    @property
    def websocket_connected(self) -> bool:
        """Return True if any WebSocket connection is active."""
        return self.api.push_connected

    @property
    def websocket_connections_info(self) -> list[dict[str, Any]]:
        """Return info about all WebSocket connections."""
        return self.api.push_connections_info

    async def _async_update_data(self) -> HarviaSaunaData:
        """Fetch data via REST API (fallback polling)."""
        try:
            device_list = await self.api.async_get_devices()
            data = HarviaSaunaData()

            for device_entry in device_list:
                device_id = device_entry["device_id"]

                # Fetch both state and latest telemetry
                state = await self.api.async_get_device_state(device_id)
                telemetry = await self.api.async_get_latest_device_data(device_id)

                # Preserve session data from previous cycle
                if (
                    self.data
                    and device_id in self.data.devices
                ):
                    device_data = self.data.devices[device_id]
                else:
                    device_data = HarviaDeviceData(device_id=device_id)

                _apply_state_data(device_data, state)
                _apply_telemetry_data(device_data, telemetry)
                _update_session_tracking(self.hass, device_data)
                _update_temp_trend(device_data)

                data.devices[device_id] = device_data

            data.available = True
            return data

        except HarviaAuthError as err:
            # Trigger reauth flow in HA UI
            raise ConfigEntryAuthFailed(
                f"Authentication error: {err}"
            ) from err
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
                    device = self.data.devices[device_id]
                    _apply_state_data(device, state)
                    _update_session_tracking(self.hass, device)
                    updated = True

        elif "onDataUpdates" in payload_data:
            item = payload_data["onDataUpdates"].get("item", {})
            device_id = item.get("deviceId")
            if device_id and device_id in self.data.devices:
                telemetry = json.loads(item.get("data", "{}"))
                telemetry["timestamp"] = item.get("timestamp")
                device = self.data.devices[device_id]
                _apply_telemetry_data(device, telemetry)
                _update_session_tracking(self.hass, device)
                _update_temp_trend(device)
                updated = True

        if updated:
            self.async_set_updated_data(self.data)

    async def async_request_state_change(
        self, device_id: str, payload: dict[str, Any]
    ) -> None:
        """Send a state change command to a device."""
        try:
            await self.api.async_request_state_change(device_id, payload)
        except HarviaAuthError as err:
            raise ConfigEntryAuthFailed(
                f"Authentication error during command: {err}"
            ) from err

    def is_device_stale(self, device_id: str) -> bool:
        """Check if a device has not received updates recently."""
        if not self.data or device_id not in self.data.devices:
            return True
        device = self.data.devices[device_id]
        if device._last_update == 0.0:
            return False  # No update yet, trust initial data
        return (time.monotonic() - device._last_update) > DEVICE_STALE_TIMEOUT


def _apply_state_data(device: HarviaDeviceData, data: dict[str, Any]) -> None:
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
    if "fwVersion" in data:
        device.firmware_version = str(data["fwVersion"])
    elif "swVersion" in data:
        device.firmware_version = str(data["swVersion"])

    device._last_update = time.monotonic()


def _apply_telemetry_data(device: HarviaDeviceData, data: dict[str, Any]) -> None:
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

    device._last_update = time.monotonic()


def _update_session_tracking(
    hass: HomeAssistant, device: HarviaDeviceData
) -> None:
    """Track sauna session start/end and fire HA events."""
    import datetime as dt

    # Reset daily counter at midnight
    today = dt.date.today().isoformat()
    if device._sessions_today_date != today:
        device.sessions_today = 0
        device._sessions_today_date = today

    now = time.monotonic()

    # Session just started
    if device.active and not device._session_active:
        device._session_active = True
        device._session_start_time = now
        device._session_max_temp = device.current_temp or 0.0
        device.sessions_today += 1

        hass.bus.async_fire(EVENT_SESSION_START, {
            "device_id": device.device_id,
            "target_temp": device.target_temp,
        })
        _LOGGER.debug(
            "Sauna session started (device %s)", device.device_id
        )

    # Session ongoing - track max temperature
    elif device.active and device._session_active:
        if device.current_temp and device.current_temp > device._session_max_temp:
            device._session_max_temp = device.current_temp

    # Session just ended
    elif not device.active and device._session_active:
        device._session_active = False

        if device._session_start_time is not None:
            duration_min = (now - device._session_start_time) / 60.0
            device.last_session_duration = round(duration_min, 1)
            device.last_session_max_temp = device._session_max_temp

            hass.bus.async_fire(EVENT_SESSION_END, {
                "device_id": device.device_id,
                "duration_min": device.last_session_duration,
                "max_temp": device.last_session_max_temp,
            })
            _LOGGER.debug(
                "Sauna session ended (device %s): %.1f min, max %.0f°C",
                device.device_id,
                device.last_session_duration,
                device.last_session_max_temp,
            )

        device._session_start_time = None
        device._session_max_temp = 0.0


def _update_temp_trend(device: HarviaDeviceData) -> None:
    """Calculate temperature change rate (°C/min)."""
    if device.current_temp is None:
        return

    now = time.monotonic()
    device._temp_history.append((now, device.current_temp))

    if len(device._temp_history) < 2:
        device.temp_trend = None
        return

    # Use oldest and newest readings for trend
    t_old, temp_old = device._temp_history[0]
    t_new, temp_new = device._temp_history[-1]

    elapsed_min = (t_new - t_old) / 60.0
    if elapsed_min < 0.1:  # Less than 6 seconds - too short
        return

    device.temp_trend = round((temp_new - temp_old) / elapsed_min, 2)
