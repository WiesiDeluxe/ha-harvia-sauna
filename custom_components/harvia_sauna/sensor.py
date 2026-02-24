"""Sensor platform for Harvia Sauna."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HarviaDeviceData, HarviaSaunaCoordinator
from .entity import HarviaBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HarviaSensorDescription(SensorEntityDescription):
    """Describe a Harvia sensor entity."""

    value_fn: Callable[[HarviaDeviceData], int | float | str | None]


SENSOR_DESCRIPTIONS: list[HarviaSensorDescription] = [
    HarviaSensorDescription(
        key="current_temperature",
        translation_key="current_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        value_fn=lambda d: d.current_temp,
    ),
    HarviaSensorDescription(
        key="humidity",
        translation_key="humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
        value_fn=lambda d: d.humidity,
    ),
    HarviaSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        icon="mdi:thermometer-chevron-up",
        value_fn=lambda d: d.target_temp,
    ),
    HarviaSensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:timer-sand",
        value_fn=lambda d: d.remaining_time if d.active else 0,
    ),
    HarviaSensorDescription(
        key="heat_up_time",
        translation_key="heat_up_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:timer-alert",
        value_fn=lambda d: d.heat_up_time,
    ),
    HarviaSensorDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi",
        value_fn=lambda d: d.wifi_rssi,
    ),
    HarviaSensorDescription(
        key="status_codes",
        translation_key="status_codes",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:information-outline",
        value_fn=lambda d: d.status_codes,
    ),
    HarviaSensorDescription(
        key="aroma_level",
        translation_key="aroma_level",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:flower",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.aroma_level,
    ),
    # Diagnostic relay counters
    HarviaSensorDescription(
        key="ph1_relay_counter",
        translation_key="ph1_relay_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=lambda d: d.ph1_relay_counter,
    ),
    HarviaSensorDescription(
        key="ph2_relay_counter",
        translation_key="ph2_relay_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=lambda d: d.ph2_relay_counter,
    ),
    HarviaSensorDescription(
        key="ph3_relay_counter",
        translation_key="ph3_relay_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=lambda d: d.ph3_relay_counter,
    ),
    HarviaSensorDescription(
        key="heat_on_counter",
        translation_key="heat_on_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=lambda d: d.heat_on_counter,
    ),
    HarviaSensorDescription(
        key="steam_on_counter",
        translation_key="steam_on_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=lambda d: d.steam_on_counter,
    ),
    HarviaSensorDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        value_fn=lambda d: d.heater_power if d.heat_on else 0,
    ),
    HarviaSensorDescription(
        key="energy",
        translation_key="energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
        value_fn=lambda d: d.energy_kwh,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Harvia sensor entities."""
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id in coordinator.data.devices:
        for description in SENSOR_DESCRIPTIONS:
            entities.append(
                HarviaSensor(coordinator, device_id, description)
            )

    async_add_entities(entities)


class HarviaSensor(HarviaBaseEntity, SensorEntity):
    """Harvia Sauna sensor entity."""

    entity_description: HarviaSensorDescription

    def __init__(
        self,
        coordinator: HarviaSaunaCoordinator,
        device_id: str,
        description: HarviaSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | float | str | None:
        """Return the sensor value."""
        device = self._get_device_data()
        if device is None:
            return None
        return self.entity_description.value_fn(device)
