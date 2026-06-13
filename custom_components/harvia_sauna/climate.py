"""Climate platform for Harvia Sauna."""

from __future__ import annotations

import logging

from homeassistant.components.climate import (
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_PRESET1_DURATION,
    CONF_PRESET1_NAME,
    CONF_PRESET1_TEMP,
    CONF_PRESET2_DURATION,
    CONF_PRESET2_NAME,
    CONF_PRESET2_TEMP,
    CONF_PRESET3_DURATION,
    CONF_PRESET3_NAME,
    CONF_PRESET3_TEMP,
    DOMAIN,
)
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


def _load_presets(entry: ConfigEntry) -> dict[str, dict]:
    """Build the preset map from options (only fully-configured slots).

    A slot counts as configured when it has a non-empty name and a
    temperature. Duration is optional (onTime in minutes).
    """
    opts = entry.options
    slots = [
        (CONF_PRESET1_NAME, CONF_PRESET1_TEMP, CONF_PRESET1_DURATION),
        (CONF_PRESET2_NAME, CONF_PRESET2_TEMP, CONF_PRESET2_DURATION),
        (CONF_PRESET3_NAME, CONF_PRESET3_TEMP, CONF_PRESET3_DURATION),
    ]
    presets: dict[str, dict] = {}
    for name_key, temp_key, dur_key in slots:
        name = (opts.get(name_key) or "").strip()
        temp = opts.get(temp_key)
        if not name or temp is None:
            continue
        presets[name] = {
            "temp": int(temp),
            "duration": int(opts[dur_key]) if opts.get(dur_key) else None,
        }
    return presets


class HarviaThermostat(HarviaBaseEntity, ClimateEntity):
    """Harvia Sauna thermostat."""

    _attr_translation_key = "thermostat"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
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
        self._presets = _load_presets(coordinator.config_entry)
        features = ClimateEntityFeature.TARGET_TEMPERATURE
        if self._presets:
            features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = [PRESET_NONE, *self._presets.keys()]
            self._attr_preset_mode = PRESET_NONE
        self._attr_supported_features = features

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

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset, matched against the target temp.

        Presets only set values; they do not start the heater. The current
        preset is inferred from the target temperature (a preset is
        "active" when the target matches its temperature).
        """
        if not self._presets:
            return None
        device = self._get_device_data()
        if device is None or device.target_temp is None:
            return PRESET_NONE
        for name, cfg in self._presets.items():
            if cfg["temp"] == int(device.target_temp):
                return name
        return PRESET_NONE

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Apply a preset: set target temperature (and duration).

        Does NOT start the heater — applying a preset only prepares the
        settings, matching the rest of the integration's explicit-control
        philosophy.
        """
        if preset_mode == PRESET_NONE or preset_mode not in self._presets:
            self._attr_preset_mode = PRESET_NONE
            self.async_write_ha_state()
            return
        cfg = self._presets[preset_mode]
        payload: dict = {"targetTemp": cfg["temp"]}
        if cfg["duration"] is not None:
            payload["onTime"] = cfg["duration"]
        await self.coordinator.async_request_state_change(
            self._device_id, payload
        )
        device = self._get_device_data()
        if device:
            device.target_temp = cfg["temp"]
        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()
