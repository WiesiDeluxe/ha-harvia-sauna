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


async def test_combi_limit_uses_the_active_profile_humidity(hass: HomeAssistant) -> None:
    """On Fenix the session humidity is 0 while off — clamp against the profile.

    PATCH /devices/target writes into the active profile, so a 95 °C setpoint
    with a 50 % profile would put the heater at 145 combined (issue #9).
    """
    entry, _ = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = next(iter(coordinator.data.devices))
    device = coordinator.data.devices[device_id]
    device.target_rh = 0  # heater off: session value carries no information
    device.active_profile = 1
    device.profiles = {"1": {"name": "Gemütlich", "targetTemp": 65, "targetHum": 50}}

    clamped = coordinator._apply_combi_limit(device_id, {"targetTemp": 95})
    assert clamped["targetRh"] == 45, "must clamp against the profile humidity"

    # An explicit humidity in the payload still wins
    explicit = coordinator._apply_combi_limit(
        device_id, {"targetTemp": 95, "targetRh": 10}
    )
    assert explicit["targetRh"] == 10


async def test_combi_limit_unchanged_on_xenio(hass: HomeAssistant) -> None:
    """Xenio has no profiles, so nothing new may be clamped."""
    entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = next(iter(coordinator.data.devices))
    coordinator.data.devices[device_id].target_rh = 0
    assert coordinator._apply_combi_limit(device_id, {"targetTemp": 95}) == {
        "targetTemp": 95
    }


async def test_session_time_uses_command_params_not_state(hass: HomeAssistant) -> None:
    """Numeric commands go in command.params (issue #10).

    The cloud rejects a number in command.state with HTTP 400 ("Use on/off,
    true/false, or 1/0 ... send command.params"), so setting the session time
    failed every time on Fenix.
    """
    sent: list[dict] = []

    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)

    async def fake_rest(service, method, path, json_data=None, **kw):
        sent.append({"path": path, "body": json_data})
        return {}

    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = next(iter(coordinator.data.devices))
    from custom_components.harvia_sauna.api_harviaio import HarviaIoApiClient

    real = HarviaIoApiClient.async_request_state_change
    client = HarviaIoApiClient.__new__(HarviaIoApiClient)
    client._async_rest_request = fake_rest
    await real(client, device_id, {"onTime": 45})

    body = next(s["body"] for s in sent if s["path"] == "/devices/command")
    assert body["command"]["type"] == "ADJUST_DURATION"
    assert "state" not in body["command"], "numeric value must not go in state"
    assert body["command"]["params"] == {"minutes": 45}


async def test_session_time_step_differs_per_controller(hass: HomeAssistant) -> None:
    """Fenix allows quarter hours; the whole-hour rule is a Xenio measurement."""
    entry, _ = await _setup(hass, API_PROVIDER_HARVIAIO)
    fenix = hass.states.get("number.sauna_session_time")
    assert fenix is not None, hass.states.async_entity_ids("number")
    assert fenix.attributes["step"] == 15


async def test_session_time_step_stays_whole_hours_on_xenio(hass: HomeAssistant) -> None:
    """The whole-hour step is a measured Xenio constraint — keep it."""
    await _setup(hass, API_PROVIDER_MYHARVIA)
    xenio = hass.states.get("number.sauna_session_time")
    assert xenio is not None
    assert xenio.attributes["step"] == 60
