"""Diagnostics support for Harvia Sauna."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .api_factory import get_provider_from_entry_data
from .const import DOMAIN
from .coordinator import HarviaSaunaCoordinator

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, "email", "organizationId"}


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

    return {
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "provider": get_provider_from_entry_data(entry.data),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "websocket_connected": coordinator.websocket_connected,
            "websocket_connections": coordinator.websocket_connections_info,
        },
        "devices": devices_info,
    }
