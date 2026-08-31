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
from homeassistant.util import dt as dt_util

from .api_factory import create_api_client, get_provider_from_entry_data
from .const import (
    SERVICE_SET_SCHEDULE,
    SERVICE_CLEAR_SCHEDULE,
    CONF_HEATER_MODEL,
    CONF_HEATER_POWER,
    DEFAULT_HEATER_POWER_W,
    DOMAIN,
    SERVICE_CANCEL_PREHEAT,
    SERVICE_READY_AT,
    SERVICE_SET_SESSION,
)
from .coordinator import TIMED_START_CLEAR, HarviaSaunaCoordinator, encode_timed_start
from .errors import HarviaAuthError, HarviaConnectionError
from .ambilight import HarviaAmbilight
from .learning import HeatingModel, heater_class_for_model
from .light_sync import HarviaLightSync
from .preheat import PreheatScheduler

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.DATETIME,
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

SERVICE_READY_AT_SCHEMA = vol.Schema(
    {
        vol.Required("ready_at"): cv.datetime,
        vol.Optional("target_temp"): vol.All(
            vol.Coerce(int), vol.Range(min=40, max=110)
        ),
        vol.Optional("device_id"): cv.string,
    }
)

SERVICE_SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("ready_at"): cv.datetime,
        vol.Required("duration"): vol.All(vol.Coerce(int), vol.Range(min=15, max=720)),
        vol.Required("target_temp"): vol.All(vol.Coerce(int), vol.Range(min=40, max=110)),
        vol.Optional("enabled", default=True): cv.boolean,
    }
)
SERVICE_CLEAR_SCHEDULE_SCHEMA = vol.Schema({vol.Required("device_id"): cv.string})
SERVICE_CANCEL_PREHEAT_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
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

    # v2.7.0: temperature-driven Ambilight (attach before platforms so the
    # Ambilight switch can report availability)
    ambilight = HarviaAmbilight(hass, coordinator, entry)
    coordinator.ambilight = ambilight

    # v2.8.0: heating model (learned) + preheat scheduler
    heater_class = heater_class_for_model(entry.data.get(CONF_HEATER_MODEL))
    heating_model = HeatingModel(hass, entry.entry_id, heater_class)
    await heating_model.async_load()
    coordinator.heating_model = heating_model

    scheduler = PreheatScheduler(hass, coordinator, entry)
    coordinator.preheat = scheduler

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up panel<->HA light sync (configured via options flow)
    light_sync = HarviaLightSync(hass, coordinator, entry)
    await light_sync.async_setup()
    entry.async_on_unload(light_sync.async_teardown)

    await ambilight.async_setup()
    entry.async_on_unload(ambilight.async_teardown)

    # Re-arm a persisted preheat schedule (after restart)
    await scheduler.async_load()
    entry.async_on_unload(scheduler.async_teardown)

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

    def _find_coordinator(device_id: str):
        for entry_data in hass.data.get(DOMAIN, {}).values():
            if entry_data.data and device_id in entry_data.data.devices:
                return entry_data
        return None

    async def async_handle_set_schedule(call: ServiceCall) -> None:
        """Program the heater's one-shot schedule (Xenio timedStart)."""
        device_id = call.data["device_id"]
        coordinator = _find_coordinator(device_id)
        if coordinator is None:
            _LOGGER.warning("set_schedule: device %s not found", device_id)
            return
        ready_at = dt_util.as_utc(call.data["ready_at"])
        b64 = encode_timed_start(
            call.data["enabled"], ready_at, call.data["duration"], call.data["target_temp"]
        )
        # Conflict guard: HA smart preheat and the device-held schedule are
        # independent paths — warn when both are planned for this device.
        try:
            preheat = getattr(coordinator, "preheat", None)
            planned = getattr(preheat, "planned_start", None)
            planned = planned(device_id) if callable(planned) else (
                planned.get(device_id) if isinstance(planned, dict) else planned
            )
            if planned:
                _LOGGER.warning(
                    "Device schedule set while HA smart preheat is also planned for %s "
                    "— both will start the heater; cancel one of them", device_id
                )
                await hass.services.async_call(
                    "persistent_notification", "create",
                    {"title": "Harvia: two schedules active",
                     "message": "A device schedule was set while the smart preheat is also planned. Both will start the heater — cancel one of them.",
                     "notification_id": f"{DOMAIN}_schedule_conflict_{device_id}"},
                    blocking=False,
                )
        except Exception:  # never let the guard break the service
            _LOGGER.debug("schedule conflict guard skipped", exc_info=True)
        await coordinator.api.async_request_state_change(device_id, {"timedStart": b64})
        await coordinator.async_request_refresh()

    async def async_handle_clear_schedule(call: ServiceCall) -> None:
        """Remove the heater's one-shot schedule (all-zero timedStart)."""
        device_id = call.data["device_id"]
        coordinator = _find_coordinator(device_id)
        if coordinator is None:
            _LOGGER.warning("clear_schedule: device %s not found", device_id)
            return
        await coordinator.api.async_request_state_change(device_id, {"timedStart": TIMED_START_CLEAR})
        await coordinator.async_request_refresh()

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

    def _find_coordinator(device_id: str | None):
        """Return (coordinator, device_id) for a device, or first coordinator."""
        coordinators = list(hass.data.get(DOMAIN, {}).values())
        if device_id:
            for coord in coordinators:
                if coord.data and device_id in coord.data.devices:
                    return coord, device_id
            return None, device_id
        # No device given: use the first coordinator and its first device
        if coordinators:
            coord = coordinators[0]
            dev = next(iter(coord.data.devices)) if coord.data and coord.data.devices else None
            return coord, dev
        return None, None

    async def async_handle_ready_at(call: ServiceCall) -> None:
        """Schedule a smart preheat to be ready at a given time."""
        device_id = call.data.get("device_id")
        coordinator, resolved = _find_coordinator(device_id)
        if coordinator is None or coordinator.preheat is None:
            _LOGGER.error("ready_at: no coordinator for device %s", device_id)
            return
        ready_at = call.data["ready_at"]
        if ready_at.tzinfo is None:
            ready_at = dt_util.as_local(ready_at)
        await coordinator.preheat.async_set_schedule(
            dt_util.as_utc(ready_at),
            call.data.get("target_temp"),
            resolved,
        )

    async def async_handle_cancel_preheat(call: ServiceCall) -> None:
        """Cancel an active preheat schedule."""
        device_id = call.data.get("device_id")
        coordinator, _ = _find_coordinator(device_id)
        if coordinator is None or coordinator.preheat is None:
            return
        await coordinator.preheat.async_cancel()

    if not hass.services.has_service(DOMAIN, SERVICE_SET_SESSION):
        hass.services.async_register(
            DOMAIN, SERVICE_SET_SCHEDULE, async_handle_set_schedule, schema=SERVICE_SET_SCHEDULE_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, SERVICE_CLEAR_SCHEDULE, async_handle_clear_schedule, schema=SERVICE_CLEAR_SCHEDULE_SCHEMA
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_SESSION,
            async_handle_set_session,
            schema=SERVICE_SET_SESSION_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_READY_AT):
        hass.services.async_register(
            DOMAIN,
            SERVICE_READY_AT,
            async_handle_ready_at,
            schema=SERVICE_READY_AT_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_CANCEL_PREHEAT):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CANCEL_PREHEAT,
            async_handle_cancel_preheat,
            schema=SERVICE_CANCEL_PREHEAT_SCHEMA,
        )
