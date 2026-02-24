"""Switch platform for Harvia Sauna."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HarviaDeviceData, HarviaSaunaCoordinator
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
    ),
    HarviaSwitchDescription(
        key="steamer",
        translation_key="steamer",
        api_key="steamEn",
        state_attr="steam_enabled",
        icon_on="mdi:weather-fog",
        icon_off="mdi:weather-fog",
    ),
    HarviaSwitchDescription(
        key="aroma",
        translation_key="aroma",
        api_key="aromaEn",
        state_attr="aroma_enabled",
        icon_on="mdi:flower",
        icon_off="mdi:flower-outline",
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
    ),
    HarviaSwitchDescription(
        key="dehumidifier",
        translation_key="dehumidifier",
        api_key="dehumEn",
        state_attr="dehumidifier_enabled",
        icon_on="mdi:air-humidifier",
        icon_off="mdi:air-humidifier-off",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Harvia switch entities."""
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id in coordinator.data.devices:
        for description in SWITCH_DESCRIPTIONS:
            entities.append(
                HarviaSwitch(coordinator, device_id, description)
            )

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
