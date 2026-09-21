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


async def test_combi_limit_humidity_write_uses_the_profile_temperature(
    hass: HomeAssistant,
) -> None:
    """Mirror case: a humidity-only write must not trust a stale session temp.

    Measured on a Fenix (issue #9): after a profile edit in the app with the
    heater off, the profile held the new temperature while target_temp kept
    the old one. With the session value the lower of the two, 60 % against a
    90 °C profile went through as 150 combined.
    """
    entry, _ = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = next(iter(coordinator.data.devices))
    device = coordinator.data.devices[device_id]
    device.target_temp = 70  # stale session value
    device.active_profile = 1
    device.profiles = {"1": {"name": "Heiß", "targetTemp": 90, "targetHum": 30}}

    clamped = coordinator._apply_combi_limit(device_id, {"targetRh": 60})
    assert clamped["targetRh"] == 50, "must clamp against the profile temperature"

    # The stale value can also be the higher one -> still the stricter clamp
    device.target_temp = 95
    assert coordinator._apply_combi_limit(device_id, {"targetRh": 60})["targetRh"] == 45

    # An explicit temperature in the payload still wins over both
    explicit = coordinator._apply_combi_limit(
        device_id, {"targetTemp": 60, "targetRh": 60}
    )
    assert explicit["targetRh"] == 60


async def test_combi_limit_unchanged_on_xenio(hass: HomeAssistant) -> None:
    """Xenio has no profiles, so nothing new may be clamped."""
    entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = next(iter(coordinator.data.devices))
    coordinator.data.devices[device_id].target_rh = 0
    assert coordinator._apply_combi_limit(device_id, {"targetTemp": 95}) == {
        "targetTemp": 95
    }


async def test_fenix_client_never_sends_a_duration_command(hass: HomeAssistant) -> None:
    """ADJUST_DURATION is not a Fenix command at all (issue #9, follow-up to #10).

    b5 moved the value from command.state to command.params.minutes, which got
    the request past the first validation only: the cloud then answers
    HTTP 400 "Command 'ADJUST_DURATION' is not supported for device type
    'Fenix'". The client must not send it, and must say so via its flag.
    """
    sent: list[dict] = []

    async def fake_rest(service, method, path, json_data=None, **kw):
        sent.append({"path": path, "body": json_data})
        return {}

    from custom_components.harvia_sauna.api_harviaio import HarviaIoApiClient

    assert HarviaIoApiClient.supports_session_duration is False
    client = HarviaIoApiClient.__new__(HarviaIoApiClient)
    client._async_rest_request = fake_rest
    await HarviaIoApiClient.async_request_state_change(
        client, "dev", {"targetTemp": 80, "onTime": 45}
    )
    assert sent, "the temperature must still be written"
    assert not [s for s in sent if "ADJUST_DURATION" in str(s["body"])]


async def test_fenix_duration_is_refused_before_anything_is_written(
    hass: HomeAssistant,
) -> None:
    """set_session with a duration must not end up half applied."""
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = next(iter(coordinator.data.devices))

    with pytest.raises(HomeAssistantError, match="heating profile"):
        await coordinator.async_request_state_change(
            device_id, {"targetTemp": 80, "onTime": 45}
        )
    assert api.writes == [], "nothing may be written when the request is refused"


async def test_fenix_preset_applies_without_its_duration(hass: HomeAssistant) -> None:
    """A preset's duration is optional; on Fenix it is skipped, not fatal."""
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    climate = hass.data["climate"].get_entity("climate.sauna_thermostat")
    climate._presets = {"Test": {"temp": 80, "duration": 90}}

    await climate.async_set_preset_mode("Test")
    assert api.writes == [(next(iter(hass.data[DOMAIN][entry.entry_id].data.devices)),
                           {"targetTemp": 80})]


async def test_xenio_preset_still_sends_its_duration(hass: HomeAssistant) -> None:
    """Xenio has a real onTime setpoint - the Fenix rule must not leak over."""
    entry, api = await _setup(hass, API_PROVIDER_MYHARVIA)
    climate = hass.data["climate"].get_entity("climate.sauna_thermostat")
    climate._presets = {"Test": {"temp": 80, "duration": 90}}

    await climate.async_set_preset_mode("Test")
    assert api.writes[-1][1] == {"targetTemp": 80, "onTime": 60}


async def test_fenix_shows_the_profile_duration_as_a_sensor(hass: HomeAssistant) -> None:
    """The duration lives in profiles.<n>.duration, so it is read-only there.

    The old number showed 360 - the dataclass default, since the Fenix shadow
    has no top-level onTime - and could never write.
    """
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    assert hass.states.get("number.sauna_session_time") is None
    sensor = hass.states.get("sensor.sauna_session_time")
    assert sensor is not None, hass.states.async_entity_ids("sensor")
    assert sensor.state == "150"  # active profile 2 ("Intensiv")

    api.active_profile = 1  # "Gemütlich", 120 min
    await _refresh(hass, entry.entry_id)
    assert hass.states.get("sensor.sauna_session_time").state == "120"

    from custom_components.harvia_sauna.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    diag = await async_get_config_entry_diagnostics(hass, entry)
    device = next(iter(diag["devices"].values()))
    assert device["on_time"] is None, "360 is a default, not a Fenix reading"


async def test_stale_fenix_session_time_number_is_removed(hass: HomeAssistant) -> None:
    """Upgrading from <= b6 must not leave the dead number behind."""
    from homeassistant.helpers import entity_registry as er

    from .test_setup import DEVICE_ID

    registry = er.async_get(hass)
    stale = registry.async_get_or_create(  # as registered by an older version
        "number", DOMAIN, f"{DEVICE_ID}_on_time", suggested_object_id="sauna_session_time"
    )
    await _setup(hass, API_PROVIDER_HARVIAIO)
    assert registry.async_get(stale.entity_id) is None


async def test_xenio_session_time_number_keeps_its_registry_entry(
    hass: HomeAssistant,
) -> None:
    """The cleanup is Fenix-only: a Xenio user's renamed number must survive."""
    from homeassistant.helpers import entity_registry as er

    from .test_setup import DEVICE_ID

    registry = er.async_get(hass)
    mine = registry.async_get_or_create(
        "number", DOMAIN, f"{DEVICE_ID}_on_time", suggested_object_id="my_sauna_minutes"
    )
    # Assert on the removal itself: Home Assistant remembers deleted entries
    # and restores the entity_id on re-creation, so the end state alone looks
    # the same whether or not the entry was wrongly removed in between.
    from unittest.mock import patch

    with patch.object(registry, "async_remove", wraps=registry.async_remove) as removed:
        await _setup(hass, API_PROVIDER_MYHARVIA)
    assert removed.call_count == 0, "Xenio entities must not be touched"
    assert registry.async_get(mine.entity_id) is not None
    assert hass.states.get("number.my_sauna_minutes") is not None


async def test_session_time_step_stays_whole_hours_on_xenio(hass: HomeAssistant) -> None:
    """The whole-hour step is a measured Xenio constraint — keep it."""
    await _setup(hass, API_PROVIDER_MYHARVIA)
    xenio = hass.states.get("number.sauna_session_time")
    assert xenio is not None
    assert xenio.attributes["step"] == 60
    assert hass.states.get("sensor.sauna_session_time") is None, "sensor is Fenix-only"
