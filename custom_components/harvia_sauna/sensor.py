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
from homeassistant.helpers.restore_state import RestoreEntity

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
        entity_registry_enabled_default=False,
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
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.wifi_rssi,
    ),
    HarviaSensorDescription(
        key="status_codes",
        translation_key="status_codes",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:information-outline",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.status_codes,
    ),
    HarviaSensorDescription(
        key="aroma_level",
        translation_key="aroma_level",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:flower",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.aroma_level,
    ),
    # Diagnostic counters (Lifetime values)
    HarviaSensorDescription(
        key="ph1_relay_counter",
        translation_key="ph1_relay_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            d.ph1_relay_counter_lt if d.ph1_relay_counter_lt > 0
            else (d.ph1_relay_counter if d.ph1_relay_counter > 0 else None)
        ),
    ),
    HarviaSensorDescription(
        key="ph2_relay_counter",
        translation_key="ph2_relay_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            d.ph2_relay_counter_lt if d.ph2_relay_counter_lt > 0
            else (d.ph2_relay_counter if d.ph2_relay_counter > 0 else None)
        ),
    ),
    HarviaSensorDescription(
        key="ph3_relay_counter",
        translation_key="ph3_relay_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            d.ph3_relay_counter_lt if d.ph3_relay_counter_lt > 0
            else (d.ph3_relay_counter if d.ph3_relay_counter > 0 else None)
        ),
    ),
    HarviaSensorDescription(
        key="heat_on_counter",
        translation_key="heat_on_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=lambda d: (
            d.heat_on_counter_lt if d.heat_on_counter_lt > 0
            else (d.heat_on_counter if d.heat_on_counter > 0 else None)
        ),
    ),
    HarviaSensorDescription(
        key="steam_on_counter",
        translation_key="steam_on_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            d.steam_on_counter_lt if d.steam_on_counter_lt > 0
            else (d.steam_on_counter if d.steam_on_counter > 0 else None)
        ),
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
    # New Fenix-specific sensors
    HarviaSensorDescription(
        key="heater_power_actual",
        translation_key="heater_power_actual",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.heater_power_actual if d.heater_power_actual > 0 else None,
    ),
    HarviaSensorDescription(
        key="main_sensor_temp",
        translation_key="main_sensor_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.main_sensor_temp,
    ),
    HarviaSensorDescription(
        key="ext_sensor_temp",
        translation_key="ext_sensor_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-low",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.ext_sensor_temp,
    ),
    HarviaSensorDescription(
        key="panel_temp",
        translation_key="panel_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-lines",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.panel_temp,
    ),
    HarviaSensorDescription(
        key="total_sessions",
        translation_key="total_sessions",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.total_sessions if d.total_sessions > 0 else None,
    ),
    HarviaSensorDescription(
        key="total_bathing_hours",
        translation_key="total_bathing_hours",
        native_unit_of_measurement="h",
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:clock-time-eight",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.total_bathing_hours if d.total_bathing_hours > 0 else None,
    ),
    HarviaSensorDescription(
        key="total_hours",
        translation_key="total_hours",
        native_unit_of_measurement="h",
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:clock-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.total_hours if d.total_hours > 0 else None,
    ),
    # Active profile status (read-only)
    HarviaSensorDescription(
        key="active_profile",
        translation_key="active_profile",
        icon="mdi:tune",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.active_profile if d.active_profile >= 0 else None,
    ),
    # Session tracking
    HarviaSensorDescription(
        key="last_session_duration",
        translation_key="last_session_duration",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:timer-check",
        value_fn=lambda d: d.last_session_duration if d.last_session_duration > 0 else None,
    ),
    HarviaSensorDescription(
        key="last_session_max_temp",
        translation_key="last_session_max_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        icon="mdi:thermometer-high",
        value_fn=lambda d: d.last_session_max_temp if d.last_session_max_temp > 0 else None,
    ),
    HarviaSensorDescription(
        key="sessions_today",
        translation_key="sessions_today",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: d.sessions_today,
    ),
    HarviaSensorDescription(
        key="temp_trend",
        translation_key="temp_trend",
        native_unit_of_measurement="°C/min",
        icon="mdi:trending-up",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.temp_trend,
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
            if description.key == "energy":
                entities.append(
                    HarviaEnergySensor(coordinator, device_id, description)
                )
            else:
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


class HarviaEnergySensor(HarviaSensor, RestoreEntity):
    """Energy sensor with state restoration across HA restarts."""

    async def async_added_to_hass(self) -> None:
        """Restore last known energy value on startup."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in ("unknown", "unavailable"):
            return

        try:
            restored_value = float(last_state.state)
        except (ValueError, TypeError):
            return

        # Write restored value back to coordinator device data
        device = self._get_device_data()
        if device is not None and restored_value > device.energy_kwh:
            device.energy_kwh = restored_value
            _LOGGER.debug(
                "Restored energy value: %.3f kWh", restored_value
            )
