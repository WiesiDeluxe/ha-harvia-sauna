"""Climate platform for Harvia Sauna."""

from __future__ import annotations

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HarviaSaunaCoordinator
from .entity import HarviaBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Harvia climate entities."""
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id in coordinator.data.devices:
        entities.append(HarviaThermostat(coordinator, device_id))

    async_add_entities(entities)


class HarviaThermostat(HarviaBaseEntity, ClimateEntity):
    """Harvia Sauna thermostat."""

    _attr_translation_key = "thermostat"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 40
    _attr_max_temp = 110
    _attr_target_temperature_step = 1

    def __init__(
        self,
        coordinator: HarviaSaunaCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the thermostat."""
        super().__init__(coordinator, device_id, "thermostat")

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        device = self._get_device_data()
        return device.current_temp if device else None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        device = self._get_device_data()
        return device.target_temp if device else None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        device = self._get_device_data()
        if device and device.active:
            return HVACMode.HEAT
        return HVACMode.OFF

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.coordinator.async_request_state_change(
                self._device_id, {"targetTemp": int(temperature)}
            )
            # Optimistic update
            device = self._get_device_data()
            if device:
                device.target_temp = int(temperature)
                self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        active = hvac_mode == HVACMode.HEAT
        await self.coordinator.async_request_state_change(
            self._device_id, {"active": int(active)}
        )
        # Optimistic update
        device = self._get_device_data()
        if device:
            device.active = active
            self.async_write_ha_state()
