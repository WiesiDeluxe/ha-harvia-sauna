"""Diagnostics support for Harvia Sauna."""

from __future__ import annotations

import json
import re
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api_factory import get_provider_from_entry_data
from .coordinator import decode_status_bits, decode_timed_start
from .const import DOMAIN
from .coordinator import HarviaSaunaCoordinator

# entry.as_dict() also carries unique_id, which is the account email for
# myHarvia and "<provider>:<email>" for harvia.io — diagnostics are routinely
# pasted into public issues, so it must be redacted too (reported in #9).
# Device payloads are vendor-defined and not enumerable, so raw payloads are
# redacted by key pattern instead of an explicit list. The Wi-Fi SSID in the
# device-info websocket message was reported this way (issue #9). The MAC
# address and serial number identify a single unit and help nobody debug.
RAW_REDACT_PATTERN = re.compile(
    r"ssid|passw|token|secret|api[-_]?key|credential|email|username"
    r"|mac[-_]?addr|serial",
    re.IGNORECASE,
)


def _redact_raw(value: Any) -> Any:
    """Recursively redact sensitive-looking keys in vendor payloads.

    Payloads carry AWSJSON, i.e. JSON encoded as a *string* (shadow
    `reported`, telemetry `data`, and the websocket buffer, which stores
    messages before they are parsed). Walking dicts and lists alone therefore
    missed the `wifissid` key inside `devicesStatesUpdateFeed.item.reported`,
    so JSON strings are decoded, redacted and re-encoded (issue #9).
    """
    if isinstance(value, dict):
        return {
            k: REDACTED if RAW_REDACT_PATTERN.search(str(k)) else _redact_raw(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_raw(v) for v in value]
    if isinstance(value, str) and value.lstrip()[:1] in ("{", "["):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return value
        return json.dumps(_redact_raw(parsed), ensure_ascii=False)
    return value


TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    "email",
    "organizationId",
    "unique_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Device data (safe to share)
    devices_info: dict[str, Any] = {}
    if coordinator.data:
        for device_id, device in coordinator.data.devices.items():
            devices_info[device_id] = {
                "display_name": device.display_name,
                "firmware_version": device.firmware_version,
                "active": device.active,
                "heat_on": device.heat_on,
                "steam_on": device.steam_on,
                "current_temp": device.current_temp,
                "target_temp": device.target_temp,
                "humidity": device.humidity,
                "target_rh": device.target_rh,
                "remaining_time": device.remaining_time,
                "on_time": device.on_time,
                "heat_up_time": device.heat_up_time,
                "door_open": device.door_open,
                "lights_on": device.lights_on,
                "fan_on": device.fan_on,
                "steam_enabled": device.steam_enabled,
                "aroma_enabled": device.aroma_enabled,
                "aroma_level": device.aroma_level,
                "auto_light": device.auto_light,
                "auto_fan": device.auto_fan,
                "dehumidifier_enabled": device.dehumidifier_enabled,
                "wifi_rssi": device.wifi_rssi,
                "status_codes": device.status_codes,
                "status_bits": decode_status_bits(device.status_codes),
                "timed_start_raw": device.timed_start,
                "timed_start": decode_timed_start(device.timed_start),
                "temp_unit": device.temp_unit,
                "heater_power": device.heater_power,
                "energy_kwh": round(device.energy_kwh, 3),
                "heat_on_counter_lt": device.heat_on_counter_lt,
                "steam_on_counter_lt": device.steam_on_counter_lt,
                "ph1_relay_counter_lt": device.ph1_relay_counter_lt,
                "ph2_relay_counter_lt": device.ph2_relay_counter_lt,
                "ph3_relay_counter_lt": device.ph3_relay_counter_lt,
                "session_active": device._session_active,
                "last_session_duration": device.last_session_duration,
                "last_session_max_temp": device.last_session_max_temp,
                "sessions_today": device.sessions_today,
                "temp_trend": device.temp_trend,
            }

    # Raw API payloads (Fenix/harvia.io client buffers these) — lets users
    # share a single diagnostics download instead of enabling debug logging.
    # Essential for mapping undocumented fields like the Fenix door sensor.
    # Each entry carries captured_at/source so a stale payload is visible
    # rather than silently misleading (issue #9).
    raw_payloads = dict(getattr(coordinator, "raw_payloads", {}))
    raw_payloads["last_websocket_messages"] = getattr(
        coordinator.api, "last_ws_messages", None
    )
    raw_payloads = _redact_raw(raw_payloads)

    return {
        "generated_at": dt_util.utcnow().isoformat(),
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "options": dict(entry.options),
        "raw_payloads": raw_payloads,
        "provider": get_provider_from_entry_data(entry.data),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "websocket_connected": coordinator.websocket_connected,
            "websocket_connections": coordinator.websocket_connections_info,
        },
        "devices": devices_info,
    }
