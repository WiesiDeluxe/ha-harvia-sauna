"""Power/energy estimate (Xenio) and the Fenix target temperature (#11).

Power: measured against a meter on the maintainer's Xenio (18 and 30 Sep
2026). Bit 8 (heating demand) stays set for the whole session, bit 5 latches
once the target is first reached; the element only runs during heat-up and
briefly in the hold phase. Counting the whole demand phase gave 27.4 kWh for a
session the meter put at 12.8-13.1 kWh; bit 8 without bit 5 gave 11.9.

Fenix target: measured by @fasdav (#9). The state's targetTemp is stale in
every state; the active profile holds the real target while the heater is off,
telemetry while it runs.
"""
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.harvia_sauna.const import (
    API_PROVIDER_HARVIAIO,
    API_PROVIDER_MYHARVIA,
    DOMAIN,
)
from custom_components.harvia_sauna.coordinator import (
    HarviaDeviceData,
    _apply_state_data,
    _apply_telemetry_data,
    estimated_heater_power_w,
)

from .test_setup import DEVICE_ID, _setup

HEATING_UP = 137484   # bits 2,3,8,11,12,17 - measured at 16:30 on 18 Sep
TARGET_REACHED = 203052  # + bits 5,16 - measured at 17:35 on 18 Sep
IDLE = 4108


def _entity(hass: HomeAssistant, domain: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{DEVICE_ID}_{key}")
    assert entity_id, f"{domain} {key} not registered"
    return entity_id


async def _refresh(hass: HomeAssistant, entry_id: str) -> None:
    await hass.data[DOMAIN][entry_id].async_refresh()
    await hass.async_block_till_done()


# ── Power / energy (Xenio) ───────────────────────────────────────────────


async def test_xenio_power_sensor_follows_heat_up_not_the_whole_session(
    hass: HomeAssistant,
) -> None:
    """Rated power during heat-up, 0 once the target was reached, 0 when idle."""
    entry, api = await _setup(hass, API_PROVIDER_MYHARVIA)
    power = _entity(hass, "sensor", "power")

    api.state_overrides = {"active": 1, "statusCodes": HEATING_UP}
    api.telemetry_overrides = {"heatOn": 1}  # the demand flag, set all session
    await _refresh(hass, entry.entry_id)
    assert hass.states.get(power).state == "10800"

    api.state_overrides = {"active": 1, "statusCodes": TARGET_REACHED}
    await _refresh(hass, entry.entry_id)
    assert hass.states.get(power).state == "0", "bit 5 set: the element is mostly off"
    assert hass.states.get("climate.sauna_thermostat").attributes["hvac_action"] == "idle"

    api.state_overrides = {"active": 0, "statusCodes": IDLE}
    api.telemetry_overrides = {"heatOn": 0}
    await _refresh(hass, entry.entry_id)
    assert hass.states.get(power).state == "0"


def test_xenio_energy_counts_only_the_heat_up_phase() -> None:
    """One hour of heat-up, then an hour holding with the demand flag still set."""
    device = HarviaDeviceData(device_id="d", heater_power=10800)
    _apply_telemetry_data(device, {"heatOn": 1})
    _apply_state_data(device, {"active": 1, "statusCodes": HEATING_UP})
    device._energy_ts -= 3600  # an hour passes, then the target is reached
    _apply_state_data(device, {"statusCodes": TARGET_REACHED})
    device._energy_ts -= 3600  # an hour of holding, heatOn still 1
    _apply_telemetry_data(device, {"heatOn": 1, "temperature": 70})
    assert round(device.energy_kwh, 2) == 10.8


def test_energy_is_integrated_from_state_updates_too() -> None:
    """On Xenio the deciding bits arrive with the state, not the telemetry."""
    device = HarviaDeviceData(device_id="d", heater_power=10800)
    device.heat_on = True
    _apply_state_data(device, {"active": 1, "statusCodes": HEATING_UP})
    first = device._energy_ts
    assert first is not None, "a state update must start the energy clock"
    assert device._energy_power_w == 10800


def test_fenix_power_estimate_is_unchanged() -> None:
    """Fenix has no status bits: rated power while heatOn, as before."""
    device = HarviaDeviceData(device_id="d", heater_power=9000)
    device.heat_on = True
    assert estimated_heater_power_w(device) == 9000
    device.heat_on = False
    assert estimated_heater_power_w(device) == 0
    device.heater_power_actual = 4200  # if a unit ever reports real power
    assert estimated_heater_power_w(device) == 4200


# ── Fenix target temperature (#11) ───────────────────────────────────────


async def test_fenix_target_comes_from_the_profile_while_off(hass: HomeAssistant) -> None:
    """Heater off: the active profile, not the stale state value (62 here)."""
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    climate = hass.states.get("climate.sauna_thermostat")
    assert climate.attributes["temperature"] == 95  # profile 2 "Intensiv"

    api.active_profile = 1  # "Gemütlich", 65 °C / 40 %
    await _refresh(hass, entry.entry_id)
    assert hass.states.get("climate.sauna_thermostat").attributes["temperature"] == 65
    assert hass.states.get(_entity(hass, "sensor", "target_temperature")).state == "65"
    device = hass.data[DOMAIN][entry.entry_id].data.devices[DEVICE_ID]
    assert device.target_rh == 40


async def test_fenix_target_comes_from_telemetry_while_heating(hass: HomeAssistant) -> None:
    """Heater on: telemetry after ignition wins; a stale state push does not."""
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = coordinator.data.devices[DEVICE_ID]

    api.state_overrides = {"active": 1, "saunaStatus": 1}
    api.telemetry_overrides = {"heatOn": 1, "targetTemp": 80, "targetHum": 20}
    await _refresh(hass, entry.entry_id)
    assert hass.states.get("climate.sauna_thermostat").attributes["temperature"] == 80
    assert device.target_rh == 20

    # A push of the (stale) shadow must not drag it back
    _apply_state_data(device, {"targetTemp": 62, "targetRh": 0})
    assert device.target_temp == 80 and device.target_rh == 20


def test_fenix_telemetry_from_before_ignition_is_not_used() -> None:
    """A target seen while off can be stale; at ignition the profile applies."""
    device = HarviaDeviceData(device_id="d")
    device.profiles = {"2": {"targetTemp": 95, "targetHum": 10}}
    device.active_profile = 2
    _apply_telemetry_data(device, {"targetTemp": 50, "targetHum": 5})  # while off
    assert device.target_temp == 95
    _apply_state_data(device, {"active": 1})  # ignition, no fresh telemetry yet
    assert device.target_temp == 95 and device.target_rh == 10
    _apply_telemetry_data(device, {"targetTemp": 90})  # first frame after ignition
    assert device.target_temp == 90


async def test_fenix_preset_is_confirmed_while_off(hass: HomeAssistant) -> None:
    """A target write lands in the active profile; show it without a re-poll."""
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = coordinator.data.devices[DEVICE_ID]

    await coordinator.async_request_state_change(DEVICE_ID, {"targetTemp": 70})
    assert device.profiles["2"]["targetTemp"] == 70
    # A partial push carrying the stale session value arrives afterwards
    _apply_state_data(device, {"targetTemp": 95})
    assert device.target_temp == 70
    assert api.writes[-1] == (DEVICE_ID, {"targetTemp": 70})


async def test_xenio_target_is_untouched(hass: HomeAssistant) -> None:
    """No profiles on Xenio: the reported target stays what it was."""
    entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    assert hass.states.get("climate.sauna_thermostat").attributes["temperature"] == 62
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_request_state_change(DEVICE_ID, {"targetTemp": 70})
    device = coordinator.data.devices[DEVICE_ID]
    assert device.profiles == {}
    _apply_state_data(device, {"targetTemp": 64})
    assert device.target_temp == 64


def test_fenix_telemetry_carries_the_humidity_target() -> None:
    """targetHum from the Fenix feed must reach the coordinator (it was dropped)."""
    from custom_components.harvia_sauna.api_harviaio import _normalize_telemetry_payload

    normalized = _normalize_telemetry_payload(
        {"data": {"temp": 60, "targetTemp": 65, "targetHum": 40, "heatOn": 1}}
    )
    assert normalized["targetTemp"] == 65 and normalized["targetHum"] == 40


def test_fenix_scheduled_wait_does_not_count_energy() -> None:
    """Waiting for a scheduled start reports heatOn 1 but nothing heats (#9)."""
    device = HarviaDeviceData(device_id="d", heater_power=9000)
    _apply_telemetry_data(device, {"heatOn": 1})
    _apply_state_data(device, {"active": 1, "saunaStatus": 5})
    device._energy_ts -= 3600  # an hour of waiting
    _apply_state_data(device, {"saunaStatus": 5})
    assert device.energy_kwh < 0.001, "an hour of waiting at 9 kW would be 9 kWh"
