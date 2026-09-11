"""Select platform for Harvia sauna device profiles (Fenix)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import API_PROVIDER_HARVIAIO, CONF_API_PROVIDER, API_PROVIDER_MYHARVIA, DOMAIN
from .coordinator import HarviaSaunaCoordinator
from .entity import HarviaBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the profile select for Fenix controllers."""
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]
    provider = entry.data.get(CONF_API_PROVIDER, API_PROVIDER_MYHARVIA)
    if provider != API_PROVIDER_HARVIAIO:
        # Xenio panels have no device profiles (no activeProfile in the shadow).
        return

    async_add_entities(
        HarviaProfileSelect(coordinator, device_id)
        for device_id in coordinator.data.devices
    )


class HarviaProfileSelect(HarviaBaseEntity, SelectEntity):
    """Select the panel's active heating profile (Fenix only).

    The panel owns the profile definitions (name, target temperature,
    humidity, duration). Home Assistant can only choose which one is active;
    editing a profile is possible at the panel only. Selecting a profile also
    applies its target temperature, so the climate setpoint follows it.
    """

    _attr_translation_key = "active_profile"
    _attr_icon = "mdi:tune-variant"

    def __init__(
        self, coordinator: HarviaSaunaCoordinator, device_id: str
    ) -> None:
        """Initialize the profile select."""
        super().__init__(coordinator, device_id, "profile_select")

    def _profiles(self) -> dict[str, dict[str, Any]]:
        device = self._get_device_data()
        profiles = getattr(device, "profiles", None) if device else None
        return profiles if isinstance(profiles, dict) else {}

    def _label(self, index: str, profile: dict[str, Any]) -> str:
        """Panel name, or a stable fallback for unnamed (free) slots."""
        name = str(profile.get("name") or "").strip()
        return name or f"Profile {index}"

    @property
    def options(self) -> list[str]:
        """Return the profile names as reported by the panel."""
        profiles = self._profiles()
        return [
            self._label(idx, profiles[idx]) for idx in sorted(profiles, key=str)
        ]

    @property
    def current_option(self) -> str | None:
        """Return the label of the currently active profile."""
        device = self._get_device_data()
        if device is None:
            return None
        profiles = self._profiles()
        idx = str(device.active_profile)
        if idx not in profiles:
            return None
        return self._label(idx, profiles[idx])

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the active profile's definition for automations."""
        device = self._get_device_data()
        profiles = self._profiles()
        if device is None or not profiles:
            return None
        active = profiles.get(str(device.active_profile), {})
        return {
            "active_index": device.active_profile,
            "target_temp": active.get("targetTemp"),
            "target_humidity": active.get("targetHum"),
            "duration_min": active.get("duration"),
            "steamer": bool(active.get("steamer", {}).get("on")),
        }

    async def async_select_option(self, option: str) -> None:
        """Activate the profile with this label."""
        profiles = self._profiles()
        for idx in sorted(profiles, key=str):
            if self._label(idx, profiles[idx]) == option:
                # One change at a time: the panel drops a second change sent
                # before it has applied the first (10-15 s, issue #9).
                await self.coordinator.async_set_active_profile(
                    self._device_id, int(idx)
                )
                return
        _LOGGER.warning("Unknown profile %r for %s", option, self._device_id)
