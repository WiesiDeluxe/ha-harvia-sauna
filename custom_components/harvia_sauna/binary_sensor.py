"""Binary sensor platform for Harvia Sauna."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HarviaDeviceData, HarviaSaunaCoordinator
from .entity import HarviaBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HarviaBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Harvia binary sensor entity."""

    value_fn: Callable[[HarviaDeviceData], bool | None]


BINARY_SENSOR_DESCRIPTIONS: list[HarviaBinarySensorDescription] = [
    HarviaBinarySensorDescription(
        key="door",
        translation_key="door",
        device_class=BinarySensorDeviceClass.DOOR,
        icon="mdi:door",
        value_fn=lambda d: d.door_open,
    ),
    HarviaBinarySensorDescription(
        key="heat_on",
        translation_key="heat_on",
        device_class=BinarySensorDeviceClass.HEAT,
        icon="mdi:fire",
        value_fn=lambda d: d.heat_on,
    ),
    HarviaBinarySensorDescription(
        key="steam_on",
        translation_key="steam_on",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:weather-fog",
        value_fn=lambda d: d.steam_on,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Harvia binary sensor entities."""
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id in coordinator.data.devices:
        for description in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(
                HarviaBinarySensor(coordinator, device_id, description)
            )

    async_add_entities(entities)


class HarviaBinarySensor(HarviaBaseEntity, BinarySensorEntity):
    """Harvia Sauna binary sensor entity."""

    entity_description: HarviaBinarySensorDescription

    def __init__(
        self,
        coordinator: HarviaSaunaCoordinator,
        device_id: str,
        description: HarviaBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        device = self._get_device_data()
        if device is None:
            return None
        return self.entity_description.value_fn(device)
