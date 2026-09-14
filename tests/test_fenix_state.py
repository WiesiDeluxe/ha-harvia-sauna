"""Fenix run-state tests (issue #9).

Measured on a Combi unit with fresh snapshots: while merely waiting for a
scheduled start, the panel already reports heater.on = 1, telemetry
heatOn = 1 and steamer.on = 1; heater.state is 0 in every state. Only
saunaStatus separates waiting (5) from heating (1).
"""
from homeassistant.core import HomeAssistant

from custom_components.harvia_sauna.const import (
    API_PROVIDER_HARVIAIO,
    API_PROVIDER_MYHARVIA,
    DOMAIN,
)

from .test_setup import _setup


async def _refresh(hass: HomeAssistant, entry_id: str) -> None:
    await hass.data[DOMAIN][entry_id].async_refresh()
    await hass.async_block_till_done()


async def test_fenix_scheduled_wait_is_not_shown_as_heating(hass: HomeAssistant) -> None:
    """saunaStatus 5 must override the armed-looking flags; 1 must not."""
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    device = next(iter(hass.data[DOMAIN][entry.entry_id].data.devices.values()))

    # Waiting: everything the panel exposes looks "on", except saunaStatus
    api.state_overrides = {"active": 1, "saunaStatus": 5}
    api.telemetry_overrides = {"heatOn": 1, "steamOn": 1}
    await _refresh(hass, entry.entry_id)
    climate = hass.states.get("climate.sauna_thermostat")
    assert climate.state == "off"
    assert climate.attributes["hvac_action"] == "off"
    assert device.heat_on is False and device.steam_on is False

    # Ignition: same flags, saunaStatus flips to 1
    api.state_overrides = {"active": 1, "saunaStatus": 1}
    await _refresh(hass, entry.entry_id)
    climate = hass.states.get("climate.sauna_thermostat")
    assert climate.state == "heat"
    assert climate.attributes["hvac_action"] == "heating"
    assert device.heat_on is True


async def test_reconcile_is_order_independent(hass: HomeAssistant) -> None:
    """Telemetry (heatOn) and state (saunaStatus) arrive on different feeds."""
    from custom_components.harvia_sauna.coordinator import (
        _apply_state_data,
        _apply_telemetry_data,
    )

    entry, _ = await _setup(hass, API_PROVIDER_HARVIAIO)
    device = next(iter(hass.data[DOMAIN][entry.entry_id].data.devices.values()))

    _apply_telemetry_data(device, {"heatOn": 1})   # telemetry first ...
    _apply_state_data(device, {"active": 1, "saunaStatus": 5})  # ... then state
    assert device.active is False and device.heat_on is False

    _apply_state_data(device, {"active": 1, "saunaStatus": 5})  # state first ...
    _apply_telemetry_data(device, {"heatOn": 1})   # ... then telemetry
    assert device.heat_on is False


async def test_xenio_is_untouched_by_the_fenix_rule(hass: HomeAssistant) -> None:
    """Xenio has no saunaStatus; an active Xenio session must stay on."""
    entry, api = await _setup(hass, API_PROVIDER_MYHARVIA)
    api.state_overrides = {"active": 1}
    api.telemetry_overrides = {"heatOn": 1}
    await _refresh(hass, entry.entry_id)
    assert hass.states.get("climate.sauna_thermostat").state == "heat"
