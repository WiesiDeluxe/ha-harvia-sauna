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
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_base import HarviaApiClientBase
from .const import (
    COMBI_TEMP_RH_LIMIT,
    CONF_COOLDOWN_HYSTERESIS,
    CONF_COOLDOWN_MAX_MINUTES,
    CONF_COOLDOWN_TEMP_SENSOR,
    CONF_EXT_SENSOR_FOR_MAX_TEMP,
    CONF_READY_FIXED_TEMP,
    CONF_READY_MODE,
    CONF_SESSION_END_MODE,
    COOLDOWN_EXT_SENSOR_GRACE_SEC,
    DEFAULT_COOLDOWN_HYSTERESIS,
    DEFAULT_COOLDOWN_MAX_MINUTES,
    DEFAULT_EXT_SENSOR_FOR_MAX_TEMP,
    DEFAULT_READY_FIXED_TEMP,
    DEFAULT_READY_MODE,
    DEFAULT_SESSION_END_MODE,
    DOMAIN,
    EVENT_READY,
    EVENT_SESSION_END,
    EVENT_SESSION_START,
    READY_MODE_FIXED,
    READY_TREND_MIN_C_PER_MIN,
    REF_TREND_HISTORY_MAX,
    SCAN_INTERVAL_FALLBACK,
    SESSION_END_COOLDOWN,
    SESSION_MIN_DURATION_SEC,
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
    # Additional temperature sensors (Fenix-specific)
    main_sensor_temp: int | None = None  # From telemetry["mainSensorTemp"]
    ext_sensor_temp: int | None = None   # From telemetry["extSensorTemp"]
    panel_temp: int | None = None        # From telemetry["panelTemp"]

    # Timers
    heat_up_time: int = 0
    remaining_time: int = 0
    on_time: int = 360  # Default max time in minutes

    # Status
    status_codes: str | None = None
    # None = no door data ever received (entity shows "unknown" instead of
    # a misleading "closed" on providers that don't deliver door status yet)
    door_open: bool | None = None
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
    heater_power_actual: int = 0  # Dynamic power from telemetry["heaterPower"]
    energy_kwh: float = 0.0  # Kumulierter Energieverbrauch in kWh
    _last_heat_on_timestamp: float | None = None  # Für Energy-Berechnung
    _last_update: float = 0.0  # monotonic timestamp of last data received

    # Session tracking
    _session_active: bool = False
    _session_start_time: float | None = None
    _session_max_temp: float = 0.0
    # Ready detection (v2.7.0): latched once per session
    ready: bool = False
    # Time-to-ready estimation from the reference temp trend
    ref_trend: float | None = None          # °C/min from reference sensor
    time_to_ready_min: float | None = None  # minutes, None = unknown
    ready_at: Any = None                    # datetime | None
    _ref_temp_history: deque = field(
        default_factory=lambda: deque(maxlen=REF_TREND_HISTORY_MAX)
    )
    # Ambilight enable flag (driven by the integration's switch entity,
    # restored via RestoreEntity in switch.py)
    ambilight_enabled: bool = True

    # Cooldown phase (session_end_mode = "cooldown"): after heater-off the
    # session keeps running until temperature drops below the frozen target
    _cooldown_active: bool = False
    _cooldown_started: float | None = None
    _frozen_target_temp: float | None = None
    _ext_sensor_last_valid: float | None = None  # monotonic ts of last valid ext reading
    last_session_duration: float = 0.0  # Minuten
    last_session_max_temp: float = 0.0  # °C
    sessions_today: int = 0
    _sessions_today_date: str = ""  # ISO date string for reset

    # Statistics (v2.8.0) — restored via RestoreEntity in sensor.py
    last_session_kwh: float | None = None      # energy delta of last session
    _session_start_kwh: float | None = None    # energy_kwh snapshot at start
    sessions_week: int = 0                      # reset Monday (ISO week)
    _sessions_week_anchor: str = ""             # ISO "year-week" string
    record_max_temp: float | None = None        # hottest session ever (°C)
    record_duration_min: float | None = None    # longest session ever (min)
    total_sessions_local: int = 0               # sessions counted locally

    # Usage statistics (lifetime totals) - Fenix-specific
    total_sessions: int = 0  # From telemetry["totalSessions"]
    total_bathing_hours: int = 0  # From telemetry["totalBathingHours"]
    total_hours: int = 0  # From telemetry["totalHours"]

    # Diagnostic timers - Fenix-specific
    after_heat_time: int = 0  # From telemetry["afterHeatTime"]
    ontime_lt: int = 0  # From telemetry["ontimeLT"]

    # Safety and control status - Fenix-specific
    safety_relay: bool = False  # From telemetry["safetyRelay"]
    # door_safety_state: bool = False  # From telemetry["doorSafetyState"] - may duplicate door_open
    active_profile: int = 0  # From state["activeProfile"] (0-3)
    sauna_status: int = 0  # From state["saunaStatus"]
    remote_allowed: bool = False  # From state["remoteAllowed"]
    demo_mode: bool = False  # From state["demoMode"]
    screen_lock: bool = False  # From state["screenLock"]["on"]

    # Temperature trend (°C/min)
    _temp_history: deque = field(
        default_factory=lambda: deque(maxlen=TEMP_HISTORY_MAX)
    )
    temp_trend: float | None = None  # °C/min


@dataclass
class SessionOptions:
    """Parsed session/cooldown options from the config entry."""

    end_mode: str = DEFAULT_SESSION_END_MODE
    ext_sensor: str | None = None
    hysteresis_c: float = DEFAULT_COOLDOWN_HYSTERESIS
    max_cooldown_sec: float = DEFAULT_COOLDOWN_MAX_MINUTES * 60
    ext_sensor_for_max_temp: bool = DEFAULT_EXT_SENSOR_FOR_MAX_TEMP
    ready_mode: str = DEFAULT_READY_MODE
    ready_fixed_temp: float = DEFAULT_READY_FIXED_TEMP


def parse_session_options(options: dict[str, Any]) -> SessionOptions:
    """Build SessionOptions from config entry options."""
    return SessionOptions(
        end_mode=options.get(CONF_SESSION_END_MODE, DEFAULT_SESSION_END_MODE),
        ext_sensor=options.get(CONF_COOLDOWN_TEMP_SENSOR) or None,
        hysteresis_c=float(
            options.get(CONF_COOLDOWN_HYSTERESIS, DEFAULT_COOLDOWN_HYSTERESIS)
        ),
        max_cooldown_sec=float(
            options.get(CONF_COOLDOWN_MAX_MINUTES, DEFAULT_COOLDOWN_MAX_MINUTES)
        )
        * 60,
        ext_sensor_for_max_temp=bool(
            options.get(
                CONF_EXT_SENSOR_FOR_MAX_TEMP, DEFAULT_EXT_SENSOR_FOR_MAX_TEMP
            )
        ),
        ready_mode=options.get(CONF_READY_MODE, DEFAULT_READY_MODE),
        ready_fixed_temp=float(
            options.get(CONF_READY_FIXED_TEMP, DEFAULT_READY_FIXED_TEMP)
        ),
    )


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

    # Optional sub-controllers, attached in __init__.py during setup
    ambilight: Any = None
    heating_model: Any = None
    preheat: Any = None

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
        self.session_options = parse_session_options(dict(config_entry.options))
        self._ext_sensor_unsub = None

    async def async_setup(self) -> None:
        """Set up real-time push updates if supported by the provider."""
        await self.api.async_start_push_updates(self._async_handle_ws_update)

        # With an external reference sensor configured, ready detection,
        # time-to-ready and the cooldown session end are all driven by that
        # sensor — listen to its updates so they react even when no Harvia
        # WS/poll update arrives at that moment.
        opts = self.session_options
        if opts.ext_sensor:
            self._ext_sensor_unsub = async_track_state_change_event(
                self.hass, [opts.ext_sensor], self._async_ext_sensor_changed
            )

    async def _async_ext_sensor_changed(self, event) -> None:
        """Re-evaluate session tracking when the external temp sensor updates."""
        if not self.data:
            return
        updated = False
        for device in self.data.devices.values():
            if device._session_active or device._cooldown_active:
                self._run_session_tracking(device)
                updated = True
        if updated:
            self.async_set_updated_data(self.data)

    def _run_session_tracking(self, device: HarviaDeviceData) -> None:
        """Run session tracking with current options and external sensor."""
        ext_temp = self._get_external_temp_c(device)
        was_active = device._session_active
        _update_session_tracking(self.hass, device, self.session_options, ext_temp)
        _update_ready_state(self.hass, device, self.session_options, ext_temp)
        _update_ref_trend_and_eta(device, self.session_options, ext_temp)

        # Feed the heating model (v2.8.0). Learn only while the session is
        # active (not during cooldown — heater is off there).
        model = getattr(self, "heating_model", None)
        if model is not None:
            if device._session_active and not was_active:
                model.reset_recording()  # fresh heat-up
            ref_temp = ext_temp if ext_temp is not None else device.current_temp
            if device._session_active and ref_temp is not None:
                model.add_sample(
                    float(ref_temp),
                    heating=bool(device.active),
                    door_open=device.door_open,
                    target_temp=device.target_temp,
                )
            elif was_active and not device._session_active:
                # Session just ended — persist what was learned
                if model.finish_recording():
                    self.hass.async_create_task(model.async_save())

    def _get_external_temp_c(self, device: HarviaDeviceData) -> float | None:
        """Read the configured external temp sensor, normalized to °C.

        Returns None if no sensor is configured, the sensor is unavailable,
        or the value cannot be parsed. Falls back to None (caller then uses
        the internal Harvia sensor) if the sensor has been invalid longer
        than COOLDOWN_EXT_SENSOR_GRACE_SEC.
        """
        opts = self.session_options
        if not opts.ext_sensor:
            return None
        state = self.hass.states.get(opts.ext_sensor)
        now = time.monotonic()
        if state is None or state.state in ("unknown", "unavailable"):
            if (
                device._ext_sensor_last_valid is not None
                and now - device._ext_sensor_last_valid
                > COOLDOWN_EXT_SENSOR_GRACE_SEC
            ):
                _LOGGER.warning(
                    "External cooldown sensor %s unavailable for >%ds — "
                    "falling back to internal Harvia sensor",
                    opts.ext_sensor,
                    COOLDOWN_EXT_SENSOR_GRACE_SEC,
                )
            return None
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            return None
        unit = state.attributes.get("unit_of_measurement", "")
        if unit in ("°F", "F"):
            value = (value - 32.0) * 5.0 / 9.0
        device._ext_sensor_last_valid = now
        return value

    async def async_shutdown(self) -> None:
        """Shut down push update connections."""
        if self._ext_sensor_unsub is not None:
            self._ext_sensor_unsub()
            self._ext_sensor_unsub = None
        await self.api.async_stop_push_updates()

    @property
    def websocket_connected(self) -> bool:
        """Return True if any push connection is active."""
        return self.api.push_connected

    @property
    def websocket_connections_info(self) -> list[dict[str, Any]]:
        """Return info about all push connections."""
        return self.api.push_connections_info

    async def _async_update_data(self) -> HarviaSaunaData:
        """Fetch data via REST API (fallback polling)."""
        _LOGGER.debug("Polling: fetching data from REST APIs")
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
                self._run_session_tracking(device_data)
                _update_temp_trend(device_data)

                data.devices[device_id] = device_data

            data.available = True
            _LOGGER.debug("Polling: successfully updated %d devices", len(data.devices))
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
            _LOGGER.debug("WebSocket update ignored: coordinator data not initialized yet")
            return

        try:
            updated = False

            if "onStateUpdated" in payload_data:
                reported = payload_data["onStateUpdated"].get("reported")
                if reported:
                    state = json.loads(reported)
                    device_id = state.get("deviceId")
                    if device_id and device_id in self.data.devices:
                        device = self.data.devices[device_id]
                        _apply_state_data(device, state)
                        self._run_session_tracking(device)
                        updated = True

            elif "onDataUpdates" in payload_data:
                item = payload_data["onDataUpdates"].get("item", {})
                device_id = item.get("deviceId")
                if device_id and device_id in self.data.devices:
                    telemetry = json.loads(item.get("data", "{}"))
                    telemetry["timestamp"] = item.get("timestamp")
                    device = self.data.devices[device_id]
                    _apply_telemetry_data(device, telemetry)
                    self._run_session_tracking(device)
                    _update_temp_trend(device)
                    updated = True

            if updated:
                self.async_set_updated_data(self.data)

        except Exception as err:
            _LOGGER.exception("Unexpected error handling WebSocket update: %s", err)

    async def async_request_state_change(
        self, device_id: str, payload: dict[str, Any]
    ) -> None:
        """Send a state change command to a device."""
        payload = self._apply_combi_limit(device_id, payload)
        try:
            await self.api.async_request_state_change(device_id, payload)
        except HarviaAuthError as err:
            raise ConfigEntryAuthFailed(
                f"Authentication error during command: {err}"
            ) from err

    def _apply_combi_limit(
        self, device_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Enforce targetTemp + targetRh <= COMBI_TEMP_RH_LIMIT.

        Only the MyHarvia app enforces this limit — the API does not, so
        values set via HA would otherwise drive a combi heater beyond spec
        (community finding). Humidity is clamped, never temperature.
        """
        if "targetTemp" not in payload and "targetRh" not in payload:
            return payload
        device = (
            self.data.devices.get(device_id) if self.data else None
        )
        try:
            temp = float(
                payload.get(
                    "targetTemp",
                    (device.target_temp if device else None) or 0,
                )
            )
            rh = float(
                payload.get(
                    "targetRh",
                    (device.target_rh if device else 0) or 0,
                )
            )
        except (TypeError, ValueError):
            return payload
        if rh <= 0 or temp + rh <= COMBI_TEMP_RH_LIMIT:
            return payload
        clamped = max(0, int(COMBI_TEMP_RH_LIMIT - temp))
        _LOGGER.warning(
            "Combi limit: targetTemp %.0f + targetRh %.0f exceeds %d — "
            "clamping humidity to %d (device %s)",
            temp,
            rh,
            COMBI_TEMP_RH_LIMIT,
            clamped,
            device_id,
        )
        payload = dict(payload)
        payload["targetRh"] = clamped
        return payload

    def is_device_stale(self, device_id: str) -> bool:
        """Check if a device has not received updates recently."""
        if not self.data or device_id not in self.data.devices:
            return True
        device = self.data.devices[device_id]
        if device._last_update == 0.0:
            return False  # No update yet, trust initial data
        return (time.monotonic() - device._last_update) > DEVICE_STALE_TIMEOUT


def _to_bool(value: Any) -> bool:
    """Convert various value types to boolean.
    
    Handles:
    - Boolean values (True/False)
    - Numeric values (1/0, non-zero)
    - String values ("true", "false", "on", "off", "1", "0")
    - None (defaults to False)
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lower = value.lower().strip()
        return lower in ("true", "on", "1", "yes", "enabled")
    # For any other type, use Python's bool() but this shouldn't happen
    return bool(value)


def _apply_state_data(device: HarviaDeviceData, data: dict[str, Any]) -> None:
    """Apply device state (reported) data to the device object."""
    if "displayName" in data:
        device.display_name = data["displayName"]
    if "deviceId" in data:
        device.device_id = data["deviceId"]
    if "active" in data:
        device.active = _to_bool(data["active"])
    if "light" in data:
        val = data["light"]
        if isinstance(val, dict):
            val = val.get("on", False)
        device.lights_on = _to_bool(val)
    if "fan" in data:
        val = data["fan"]
        if isinstance(val, dict):
            val = val.get("on", False)
        device.fan_on = _to_bool(val)
    if "steamEn" in data:
        device.steam_enabled = _to_bool(data["steamEn"])
    if "targetTemp" in data:
        device.target_temp = data["targetTemp"]
    if "targetRh" in data:
        device.target_rh = data["targetRh"]
    if "heatUpTime" in data:
        device.heat_up_time = data["heatUpTime"]
    if "onTime" in data:
        device.on_time = data["onTime"]
    if "dehumEn" in data:
        device.dehumidifier_enabled = _to_bool(data["dehumEn"])
    if "autoLight" in data:
        device.auto_light = _to_bool(data["autoLight"])
    if "autoFan" in data:
        device.auto_fan = _to_bool(data["autoFan"])
    if "tempUnit" in data:
        device.temp_unit = data["tempUnit"]
    if "aromaEn" in data:
        device.aroma_enabled = _to_bool(data["aromaEn"])
    if "aromaLevel" in data:
        device.aroma_level = data["aromaLevel"]
    if "statusCodes" in data:
        device.status_codes = str(data["statusCodes"])
        # Parse door status from status codes (2nd digit = 9 means door open)
        # — Xenio only; Fenix delivers a normalized "doorOpen" key instead
        try:
            device.door_open = int(str(data["statusCodes"])[1]) == 9
        except (IndexError, ValueError):
            pass
    if "doorOpen" in data:
        device.door_open = _to_bool(data["doorOpen"])
    if "fwVersion" in data:
        device.firmware_version = str(data["fwVersion"])
    elif "swVersion" in data:
        device.firmware_version = str(data["swVersion"])

    # New Fenix-specific state fields
    if "activeProfile" in data:
        device.active_profile = data["activeProfile"]
    if "saunaStatus" in data:
        device.sauna_status = data["saunaStatus"]
    if "remoteAllowed" in data:
        device.remote_allowed = bool(data["remoteAllowed"])
    if "demoMode" in data:
        device.demo_mode = bool(data["demoMode"])
    if "screenLock" in data:
        # Handle nested structure if present
        if isinstance(data["screenLock"], dict):
            device.screen_lock = bool(data["screenLock"].get("on", False))
        else:
            device.screen_lock = bool(data["screenLock"])

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

    # New Fenix-specific telemetry fields
    if "heaterPower" in data:
        device.heater_power_actual = data["heaterPower"]
    if "mainSensorTemp" in data:
        device.main_sensor_temp = data["mainSensorTemp"]
    if "extSensorTemp" in data:
        device.ext_sensor_temp = data["extSensorTemp"]
    if "panelTemp" in data:
        device.panel_temp = data["panelTemp"]
    if "totalSessions" in data:
        device.total_sessions = data["totalSessions"]
    if "totalBathingHours" in data:
        device.total_bathing_hours = data["totalBathingHours"]
    if "totalHours" in data:
        device.total_hours = data["totalHours"]
    if "afterHeatTime" in data:
        device.after_heat_time = data["afterHeatTime"]
    if "ontimeLT" in data:
        device.ontime_lt = data["ontimeLT"]
    if "safetyRelay" in data:
        device.safety_relay = bool(data["safetyRelay"])
    if "doorOpen" in data:
        device.door_open = _to_bool(data["doorOpen"])
    # Real-time light and fan status from telemetry (overrides state if present)
    if "lightOn" in data:
        device.lights_on = bool(data["lightOn"])
    if "fanOn" in data:
        device.fan_on = bool(data["fanOn"])

    device._last_update = time.monotonic()


def _update_session_tracking(
    hass: HomeAssistant,
    device: HarviaDeviceData,
    opts: SessionOptions,
    ext_temp_c: float | None,
) -> None:
    """Track sauna session start/end and fire HA events.

    Two end modes:
    - heater_off (default): session ends when the heater turns off.
    - cooldown: after heater-off the session continues until the reference
      temperature drops below the target temperature (frozen at heater-off)
      minus a hysteresis. Designed for stone-heavy heaters (e.g. Legend with
      100 kg stones) that keep the cabin sauna-hot long after power-off.

    Reference temperature: the configured external HA sensor if available,
    otherwise the internal Harvia sensor. Sessions shorter than
    SESSION_MIN_DURATION_SEC are ignored to filter out state bounces.
    """
    import datetime as dt

    # Reset daily counter at midnight
    today = dt.date.today().isoformat()
    if device._sessions_today_date != today:
        device.sessions_today = 0
        device._sessions_today_date = today

    now = time.monotonic()
    cooldown_mode = opts.end_mode == SESSION_END_COOLDOWN

    # Reference temperature for cooldown decisions and (optionally) max temp
    ref_temp = ext_temp_c if ext_temp_c is not None else device.current_temp

    # ── Session start / re-heat during cooldown ─────────────────────
    if device.active and not device._session_active:
        device._session_active = True
        device._session_start_time = now
        device._session_max_temp = (
            ref_temp if (opts.ext_sensor_for_max_temp and ext_temp_c is not None)
            else device.current_temp
        ) or 0.0
        device._cooldown_active = False
        device._cooldown_started = None
        device._frozen_target_temp = None
        device.ready = False  # re-arm ready detection for the new session
        device._session_start_kwh = device.energy_kwh  # for last_session_kwh

        hass.bus.async_fire(EVENT_SESSION_START, {
            "device_id": device.device_id,
            "target_temp": device.target_temp,
        })
        _LOGGER.debug("Sauna session started (device %s)", device.device_id)
        return

    if device.active and device._session_active:
        if device._cooldown_active:
            # Heater re-activated during cooldown (e.g. re-heat for another
            # round) — same session continues, cooldown is cancelled
            device._cooldown_active = False
            device._cooldown_started = None
            device._frozen_target_temp = None
            _LOGGER.debug(
                "Heater re-activated during cooldown — continuing session "
                "(device %s)",
                device.device_id,
            )
        # Session ongoing — track max temperature
        _track_max_temp(device, opts, ext_temp_c)
        return

    # ── Heater just turned off ───────────────────────────────────────
    if not device.active and device._session_active and not device._cooldown_active:
        if not cooldown_mode:
            _end_session(hass, device, now)
            return

        # Cooldown mode: freeze the target temp and keep the session running.
        # If the cabin is already below target (short session that never
        # reached temperature), the end condition is met immediately —
        # behaves exactly like heater_off mode in that case.
        frozen = device.target_temp
        if frozen is None or ref_temp is None:
            # Without a target or reference there is nothing to wait for
            _end_session(hass, device, now)
            return
        device._cooldown_active = True
        device._cooldown_started = now
        device._frozen_target_temp = float(frozen)
        _LOGGER.debug(
            "Heater off — cooldown phase started (device %s, frozen target "
            "%.1f°C, hysteresis %.1f°C, ref %.1f°C)",
            device.device_id,
            device._frozen_target_temp,
            opts.hysteresis_c,
            ref_temp,
        )
        # fall through to cooldown evaluation below

    # ── Cooldown phase evaluation ────────────────────────────────────
    if device._session_active and device._cooldown_active:
        _track_max_temp(device, opts, ext_temp_c)

        # Safety net: hard stop after max cooldown duration
        if (
            device._cooldown_started is not None
            and now - device._cooldown_started > opts.max_cooldown_sec
        ):
            _LOGGER.debug(
                "Max cooldown duration exceeded — ending session (device %s)",
                device.device_id,
            )
            _end_session(hass, device, now)
            return

        # No usable reference temperature right now → hold state, decide later
        if ref_temp is None or device._frozen_target_temp is None:
            return

        threshold = device._frozen_target_temp - opts.hysteresis_c
        if float(ref_temp) < threshold:
            _LOGGER.debug(
                "Reference temp %.1f°C below threshold %.1f°C — ending "
                "session (device %s)",
                float(ref_temp),
                threshold,
                device.device_id,
            )
            _end_session(hass, device, now)


def _track_max_temp(
    device: HarviaDeviceData, opts: SessionOptions, ext_temp_c: float | None
) -> None:
    """Update the session max temperature from the configured source."""
    if opts.ext_sensor_for_max_temp and ext_temp_c is not None:
        if ext_temp_c > device._session_max_temp:
            device._session_max_temp = ext_temp_c
    elif device.current_temp and device.current_temp > device._session_max_temp:
        device._session_max_temp = device.current_temp


def _end_session(
    hass: HomeAssistant, device: HarviaDeviceData, now: float
) -> None:
    """Finalize a session: compute duration, fire event, reset tracking."""
    device._session_active = False
    device._cooldown_active = False
    device._cooldown_started = None
    device._frozen_target_temp = None

    if device._session_start_time is not None:
        duration_sec = now - device._session_start_time
        duration_min = duration_sec / 60.0

        if duration_sec >= SESSION_MIN_DURATION_SEC:
            # Real session — update public values
            device.last_session_duration = round(duration_min, 1)
            device.last_session_max_temp = device._session_max_temp
            device.sessions_today += 1

            # ── Statistics (v2.8.0) ──────────────────────────────────
            if device._session_start_kwh is not None:
                delta = device.energy_kwh - device._session_start_kwh
                device.last_session_kwh = round(max(0.0, delta), 2)
            import datetime as _dt
            iso = _dt.datetime.now().isocalendar()
            week_anchor = f"{iso[0]}-{iso[1]:02d}"
            if device._sessions_week_anchor != week_anchor:
                device._sessions_week_anchor = week_anchor
                device.sessions_week = 0
            device.sessions_week += 1
            device.total_sessions_local += 1
            if (
                device.record_max_temp is None
                or device.last_session_max_temp > device.record_max_temp
            ):
                device.record_max_temp = device.last_session_max_temp
            if (
                device.record_duration_min is None
                or device.last_session_duration > device.record_duration_min
            ):
                device.record_duration_min = device.last_session_duration

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
        else:
            _LOGGER.debug(
                "Sauna session too short (%.0fs < %ds), ignoring bounce "
                "(device %s)",
                duration_sec,
                SESSION_MIN_DURATION_SEC,
                device.device_id,
            )

    device._session_start_time = None
    device._session_max_temp = 0.0
    device.ready = False
    device.time_to_ready_min = None
    device.ready_at = None


def _ready_threshold(
    device: HarviaDeviceData, opts: SessionOptions
) -> float | None:
    """Return the ready threshold in °C, or None if undeterminable."""
    if opts.ready_mode == READY_MODE_FIXED:
        return opts.ready_fixed_temp
    return float(device.target_temp) if device.target_temp is not None else None


def _update_ready_state(
    hass: HomeAssistant,
    device: HarviaDeviceData,
    opts: SessionOptions,
    ext_temp_c: float | None,
) -> None:
    """Latch the per-session ready flag and fire the ready event once.

    Ready = reference temperature reached the threshold (target temp or a
    fixed value, per options). Latched: stays True until the session ends,
    even if the temperature dips afterwards (door opening etc.).
    """
    if not device._session_active or device.ready:
        return
    threshold = _ready_threshold(device, opts)
    ref_temp = ext_temp_c if ext_temp_c is not None else device.current_temp
    if threshold is None or ref_temp is None:
        return
    if float(ref_temp) >= threshold:
        device.ready = True
        device.time_to_ready_min = 0.0
        device.ready_at = None
        hass.bus.async_fire(EVENT_READY, {
            "device_id": device.device_id,
            "temperature": round(float(ref_temp), 1),
            "threshold": round(threshold, 1),
        })
        _LOGGER.debug(
            "Sauna ready: %.1f°C >= %.1f°C (device %s)",
            float(ref_temp),
            threshold,
            device.device_id,
        )


def _update_ref_trend_and_eta(
    device: HarviaDeviceData,
    opts: SessionOptions,
    ext_temp_c: float | None,
) -> None:
    """Maintain the reference-temp trend and the time-to-ready estimate.

    Uses the external sensor when configured (numerator AND denominator
    from the same source — mixing the fast external sensor with the slow
    internal trend systematically skews the ETA).
    """
    import datetime as dt

    ref_temp = ext_temp_c if ext_temp_c is not None else device.current_temp
    if ref_temp is None:
        return

    now = time.monotonic()
    device._ref_temp_history.append((now, float(ref_temp)))

    if len(device._ref_temp_history) >= 2:
        t_old, temp_old = device._ref_temp_history[0]
        t_new, temp_new = device._ref_temp_history[-1]
        elapsed_min = (t_new - t_old) / 60.0
        if elapsed_min >= 0.1:
            device.ref_trend = round((temp_new - temp_old) / elapsed_min, 2)

    # ETA only while actively heating towards the threshold
    if not device._session_active or device.ready or device._cooldown_active:
        if not device._session_active:
            device.time_to_ready_min = None
            device.ready_at = None
        return

    threshold = _ready_threshold(device, opts)
    trend = device.ref_trend
    if (
        threshold is None
        or trend is None
        or trend <= READY_TREND_MIN_C_PER_MIN
    ):
        device.time_to_ready_min = None
        device.ready_at = None
        return

    remaining_c = threshold - float(ref_temp)
    if remaining_c <= 0:
        device.time_to_ready_min = 0.0
        device.ready_at = dt.datetime.now(dt.timezone.utc)
        return
    minutes = remaining_c / trend
    device.time_to_ready_min = round(minutes, 0)
    device.ready_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
        minutes=minutes
    )


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
