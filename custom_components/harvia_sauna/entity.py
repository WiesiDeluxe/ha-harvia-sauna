"""Base entity for Harvia Sauna integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_HEATER_MODEL,
    CONF_HEATER_POWER,
    DOMAIN,
    HEATER_MODELS,
    MANUFACTURER,
)
from .coordinator import HarviaDeviceData, HarviaSaunaCoordinator, HarviaSaunaData


class HarviaBaseEntity(CoordinatorEntity[HarviaSaunaCoordinator]):
    """Base class for Harvia Sauna entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HarviaSaunaCoordinator,
        device_id: str,
        entity_key: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{entity_key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device_data = self._get_device_data()

        # Get heater model and power from config entry
        config_data = self.coordinator.config_entry.data if self.coordinator.config_entry else {}
        model_key = config_data.get(CONF_HEATER_MODEL, "other")
        model_name = HEATER_MODELS.get(model_key, "Harvia Sauna")
        power_kw = config_data.get(CONF_HEATER_POWER, "")
        model_string = f"{model_name} {power_kw} kW" if power_kw else model_name

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_data.display_name if device_data else "Harvia Sauna",
            manufacturer=MANUFACTURER,
            model=model_string,
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.available
            and self._device_id in self.coordinator.data.devices
        )

    def _get_device_data(self) -> HarviaDeviceData | None:
        """Get the device data for this entity."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.devices.get(self._device_id)
