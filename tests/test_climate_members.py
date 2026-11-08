"""HA 2026.11 renamed the climate temperature members to native_* and warns
about the old ones ("stops working in 2027.11"). The thermostat provides both,
so it works on older versions and newer ones without the warning.
"""
import pytest
from homeassistant.core import HomeAssistant

from custom_components.harvia_sauna.climate import HarviaThermostat
from custom_components.harvia_sauna.const import (
    API_PROVIDER_HARVIAIO,
    API_PROVIDER_MYHARVIA,
    DOMAIN,
)

from .test_setup import _setup


@pytest.mark.parametrize("provider", [API_PROVIDER_MYHARVIA, API_PROVIDER_HARVIAIO])
async def test_thermostat_shows_its_temperatures(hass: HomeAssistant, provider: str) -> None:
    entry, _ = await _setup(hass, provider)
    climate = hass.states.get("climate.sauna_thermostat")
    device = next(iter(hass.data[DOMAIN][entry.entry_id].data.devices.values()))
    assert device.current_temp is not None and device.target_temp is not None
    assert climate.attributes["current_temperature"] == device.current_temp
    assert climate.attributes["temperature"] == device.target_temp


def test_thermostat_provides_the_old_and_the_native_names() -> None:
    members = vars(HarviaThermostat)
    for native, old in (
        ("_attr_native_temperature_unit", "_attr_temperature_unit"),
        ("native_current_temperature", "current_temperature"),
        ("native_target_temperature", "target_temperature"),
    ):
        assert native in members, f"{native} missing: HA 2026.11+ warns, 2027.11 breaks"
        assert old in members, f"{old} missing: HA before 2026.11 shows nothing"
