"""Number platform for Harvia Sauna."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HarviaDeviceData, HarviaSaunaCoordinator
from .entity import HarviaBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HarviaNumberDescription(NumberEntityDescription):
    """Describe a Harvia number entity."""

    api_key: str
    state_attr: str
    value_fn: Callable[[HarviaDeviceData], float | None]


NUMBER_DESCRIPTIONS: list[HarviaNumberDescription] = [
    HarviaNumberDescription(
        key="target_humidity",
        translation_key="target_humidity",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        icon="mdi:cloud-percent",
        api_key="targetRh",
        state_attr="target_rh",
        value_fn=lambda d: d.target_rh,
    ),
    HarviaNumberDescription(
        key="aroma_level",
        translation_key="aroma_level_set",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        icon="mdi:flower",
        api_key="aromaLevel",
        state_attr="aroma_level",
        value_fn=lambda d: d.aroma_level,
    ),
    HarviaNumberDescription(
        key="on_time",
        translation_key="on_time",
        native_unit_of_measurement="min",
        native_min_value=0,
        native_max_value=720,
        native_step=1,
        icon="mdi:timer-cog",
        api_key="onTime",
        state_attr="on_time",
        value_fn=lambda d: d.on_time,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Harvia number entities."""
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id in coordinator.data.devices:
        for description in NUMBER_DESCRIPTIONS:
            entities.append(
                HarviaNumber(coordinator, device_id, description)
            )

    async_add_entities(entities)


class HarviaNumber(HarviaBaseEntity, NumberEntity):
    """Harvia Sauna number entity."""

    entity_description: HarviaNumberDescription

    def __init__(
        self,
        coordinator: HarviaSaunaCoordinator,
        device_id: str,
        description: HarviaNumberDescription,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        device = self._get_device_data()
        if device is None:
            return None
        return self.entity_description.value_fn(device)

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        await self.coordinator.async_request_state_change(
            self._device_id, {self.entity_description.api_key: int(value)}
        )
        # Optimistic update
        device = self._get_device_data()
        if device:
            setattr(device, self.entity_description.state_attr, int(value))
            self.async_write_ha_state()
