"""The Harvia Sauna integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from .api_factory import create_api_client, get_provider_from_entry_data
from .const import (
    CONF_HEATER_POWER,
    DEFAULT_HEATER_POWER_W,
    DOMAIN,
    SERVICE_SET_SESSION,
)
from .coordinator import HarviaSaunaCoordinator
from .errors import HarviaAuthError, HarviaConnectionError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

SERVICE_SET_SESSION_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("target_temp"): vol.All(
            vol.Coerce(int), vol.Range(min=40, max=110)
        ),
        vol.Optional("duration"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=720)
        ),
        vol.Optional("active"): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Harvia Sauna from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    # Create API client via provider factory
    provider = get_provider_from_entry_data(entry.data)
    api = create_api_client(hass, username, password, provider)

    # Authenticate
    try:
        await api.async_authenticate()
    except HarviaAuthError as err:
        raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
    except HarviaConnectionError as err:
        raise ConfigEntryNotReady(f"Connection error: {err}") from err

    # Create and initialize coordinator
    coordinator = HarviaSaunaCoordinator(hass, api, entry)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Apply configured heater power to devices
    _apply_heater_power(coordinator, entry)

    # Start WebSocket connections for real-time updates
    await coordinator.async_setup()

    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (once)
    _async_register_services(hass)

    # Listen for config entry updates (reconfigure flow)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Stop WebSocket connections
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_shutdown()

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Clean up stored data
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle config entry updates (e.g. from reconfigure flow)."""
    await hass.config_entries.async_reload(entry.entry_id)


def _apply_heater_power(
    coordinator: HarviaSaunaCoordinator, entry: ConfigEntry
) -> None:
    """Apply configured heater power to all devices."""
    power_kw_str = entry.data.get(CONF_HEATER_POWER, "")
    try:
        heater_power_w = int(float(power_kw_str) * 1000)
    except (ValueError, TypeError):
        heater_power_w = DEFAULT_HEATER_POWER_W

    if coordinator.data:
        for device in coordinator.data.devices.values():
            device.heater_power = heater_power_w


def _async_register_services(hass: HomeAssistant) -> None:
    """Register custom services for Harvia Sauna."""

    async def async_handle_set_session(call: ServiceCall) -> None:
        """Handle the set_session service call."""
        device_id = call.data["device_id"]
        payload: dict[str, Any] = {}

        if "target_temp" in call.data:
            payload["targetTemp"] = call.data["target_temp"]
        if "duration" in call.data:
            payload["onTime"] = call.data["duration"]
        if "active" in call.data:
            payload["active"] = int(call.data["active"])

        if not payload:
            _LOGGER.warning("set_session called without any parameters")
            return

        # Find the coordinator that manages this device
        for entry_data in hass.data.get(DOMAIN, {}).values():
            coordinator: HarviaSaunaCoordinator = entry_data
            if (
                coordinator.data
                and device_id in coordinator.data.devices
            ):
                await coordinator.async_request_state_change(
                    device_id, payload
                )
                return

        _LOGGER.error("Device %s not found in any coordinator", device_id)

    if not hass.services.has_service(DOMAIN, SERVICE_SET_SESSION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_SESSION,
            async_handle_set_session,
            schema=SERVICE_SET_SESSION_SCHEMA,
        )
