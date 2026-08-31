"""Switch platform for Harvia Sauna."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import datetime
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import API_PROVIDER_MYHARVIA, CONF_API_PROVIDER, DOMAIN
from .coordinator import decode_timed_start, encode_timed_start, schedule_state, HarviaDeviceData, HarviaSaunaCoordinator
from .entity import HarviaBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HarviaSwitchDescription(SwitchEntityDescription):
    """Describe a Harvia switch entity."""

    api_key: str  # Key sent to API for state change
    state_attr: str  # Attribute on HarviaDeviceData for current state
    icon_on: str = ""
    icon_off: str = ""


SWITCH_DESCRIPTIONS: list[HarviaSwitchDescription] = [
    HarviaSwitchDescription(
        key="power",
        translation_key="power",
        api_key="active",
        state_attr="active",
        icon_on="mdi:radiator",
        icon_off="mdi:radiator-off",
    ),
    HarviaSwitchDescription(
        key="light",
        translation_key="light",
        api_key="light",
        state_attr="lights_on",
        icon_on="mdi:lightbulb-on",
        icon_off="mdi:lightbulb-off",
    ),
    HarviaSwitchDescription(
        key="fan",
        translation_key="fan",
        api_key="fan",
        state_attr="fan_on",
        icon_on="mdi:fan",
        icon_off="mdi:fan-off",
        entity_registry_enabled_default=False,
    ),
    HarviaSwitchDescription(
        key="steamer",
        translation_key="steamer",
        api_key="steamEn",
        state_attr="steam_enabled",
        icon_on="mdi:weather-fog",
        icon_off="mdi:weather-fog",
        entity_registry_enabled_default=False,
    ),
    HarviaSwitchDescription(
        key="aroma",
        translation_key="aroma",
        api_key="aromaEn",
        state_attr="aroma_enabled",
        icon_on="mdi:flower",
        icon_off="mdi:flower-outline",
        entity_registry_enabled_default=False,
    ),
    HarviaSwitchDescription(
        key="auto_light",
        translation_key="auto_light",
        api_key="autoLight",
        state_attr="auto_light",
        icon_on="mdi:lightbulb-auto",
        icon_off="mdi:lightbulb-auto-outline",
    ),
    HarviaSwitchDescription(
        key="auto_fan",
        translation_key="auto_fan",
        api_key="autoFan",
        state_attr="auto_fan",
        icon_on="mdi:fan-auto",
        icon_off="mdi:fan-auto",
        entity_registry_enabled_default=False,
    ),
    HarviaSwitchDescription(
        key="dehumidifier",
        translation_key="dehumidifier",
        api_key="dehumEn",
        state_attr="dehumidifier_enabled",
        icon_on="mdi:air-humidifier",
        icon_off="mdi:air-humidifier-off",
        entity_registry_enabled_default=False,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Harvia switch entities."""
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]
    provider = entry.data.get(CONF_API_PROVIDER, API_PROVIDER_MYHARVIA)

    entities: list = []
    for device_id in coordinator.data.devices:
        for description in SWITCH_DESCRIPTIONS:
            entities.append(
                HarviaSwitch(coordinator, device_id, description)
            )
        # v2.7.0: integration-level Ambilight enable switch (local, no API)
        entities.append(HarviaAmbilightSwitch(coordinator, device_id))
        # v2.9.0: device-held schedule arm switch (Xenio timedStart byte 0)
        if provider == API_PROVIDER_MYHARVIA:
            entities.append(HarviaScheduleSwitch(coordinator, device_id))

    async_add_entities(entities)


class HarviaSwitch(HarviaBaseEntity, SwitchEntity):
    """Harvia Sauna switch entity."""

    entity_description: HarviaSwitchDescription

    def __init__(
        self,
        coordinator: HarviaSaunaCoordinator,
        device_id: str,
        description: HarviaSwitchDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        device = self._get_device_data()
        if device is None:
            return None
        return getattr(device, self.entity_description.state_attr, False)

    @property
    def icon(self) -> str:
        """Return the icon based on state."""
        desc = self.entity_description
        if self.is_on:
            return desc.icon_on or desc.icon
        return desc.icon_off or desc.icon

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.coordinator.async_request_state_change(
            self._device_id, {self.entity_description.api_key: 1}
        )
        # Optimistic update
        device = self._get_device_data()
        if device:
            setattr(device, self.entity_description.state_attr, True)
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.coordinator.async_request_state_change(
            self._device_id, {self.entity_description.api_key: 0}
        )
        # Optimistic update
        device = self._get_device_data()
        if device:
            setattr(device, self.entity_description.state_attr, False)
            self.async_write_ha_state()


class HarviaAmbilightSwitch(HarviaBaseEntity, SwitchEntity, RestoreEntity):
    """Enable/disable the temperature-driven Ambilight.

    Local integration switch (no Harvia API interaction). Turning it on
    re-arms Ambilight after a manual color override; turning it off
    restores the configured everyday standard on the zone lights.
    """

    _attr_translation_key = "ambilight"
    _attr_icon = "mdi:palette"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: HarviaSaunaCoordinator, device_id: str
    ) -> None:
        """Initialize the Ambilight switch."""
        super().__init__(coordinator, device_id, "ambilight")

    async def async_added_to_hass(self) -> None:
        """Restore the previous enable state."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            device = self._get_device_data()
            if device is not None:
                device.ambilight_enabled = last_state.state == "on"

    @property
    def is_on(self) -> bool | None:
        """Return True if Ambilight is enabled."""
        device = self._get_device_data()
        if device is None:
            return None
        return device.ambilight_enabled

    @property
    def available(self) -> bool:
        """Available only when Ambilight zones are configured."""
        ambilight = getattr(self.coordinator, "ambilight", None)
        return ambilight is not None and ambilight.configured

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Ambilight and clear a manual override."""
        device = self._get_device_data()
        if device is not None:
            device.ambilight_enabled = True
        ambilight = getattr(self.coordinator, "ambilight", None)
        if ambilight is not None:
            ambilight.async_rearm()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Ambilight and restore the everyday standard."""
        device = self._get_device_data()
        if device is not None:
            device.ambilight_enabled = False
        ambilight = getattr(self.coordinator, "ambilight", None)
        if ambilight is not None:
            await ambilight.async_restore_standard()
        self.async_write_ha_state()


class HarviaScheduleSwitch(HarviaBaseEntity, SwitchEntity):
    """Arm/disarm the heater's one-shot schedule without touching the plan.

    Mirrors the MyHarvia app toggle: disabling keeps ready time, duration and
    temperature (byte 0 only). A consumed schedule (ready_at in the past) is
    reported off. Turning on without a stored plan is refused with a warning
    — use the set_schedule service to create one.
    """

    _attr_translation_key = "scheduled_start"
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self, coordinator: HarviaSaunaCoordinator, device_id: str
    ) -> None:
        """Initialize the schedule arm switch."""
        super().__init__(coordinator, device_id, "scheduled_start")

    @property
    def is_on(self) -> bool | None:
        device = self._get_device_data()
        if device is None:
            return None
        return schedule_state(device)[0] is not None

    async def _write_enabled(self, enabled: bool) -> None:
        device = self._get_device_data()
        dec = decode_timed_start(device.timed_start) if device else None
        if not dec:
            _LOGGER.warning(
                "No stored schedule on %s — use harvia_sauna.set_schedule first",
                self._device_id,
            )
            return
        b64 = encode_timed_start(
            enabled,
            datetime.fromisoformat(dec["ready_at"]),
            dec["duration_min"],
            dec["target_temp"],
        )
        await self.coordinator.async_request_state_change(
            self._device_id, {"timedStart": b64}
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._write_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write_enabled(False)
