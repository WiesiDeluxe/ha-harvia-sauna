"""Ambilight: a manual color must win, and a switched-off Ambilight must not
repaint the lights at session end (code review, 22 Oct 2026)."""
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.harvia_sauna.ambilight import HarviaAmbilight
from custom_components.harvia_sauna.const import (
    AMBI_CURVE_FULL,
    CONF_AMBI_ZONE1_CURVE,
    CONF_AMBI_ZONE1_LIGHTS,
    DOMAIN,
    EVENT_SESSION_END,
)

LIGHT = "light.sauna_strip"


class _Coordinator:
    """Just what Ambilight reads from the coordinator."""

    def __init__(self, device) -> None:
        self.data = SimpleNamespace(devices={"d": device})
        self._listeners = []

    def async_add_listener(self, cb):
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def _get_external_temp_c(self, device):
        return None

    def update(self):
        for cb in list(self._listeners):
            cb()


async def _ambilight(hass: HomeAssistant, enabled: bool = True):
    device = SimpleNamespace(
        _session_active=True, _cooldown_active=False, ambilight_enabled=enabled,
        current_temp=60.0, _ext_sensor_holding=False,
    )
    coordinator = _Coordinator(device)
    entry = MockConfigEntry(domain=DOMAIN, options={
        CONF_AMBI_ZONE1_LIGHTS: [LIGHT], CONF_AMBI_ZONE1_CURVE: AMBI_CURVE_FULL,
    })
    calls = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set(LIGHT, "on", {"hs_color": [30.0, 80.0]})
    ambilight = HarviaAmbilight(hass, coordinator, entry)
    await ambilight.async_setup()
    return ambilight, coordinator, calls


async def test_a_manual_color_wins_after_updates_without_a_new_color(
    hass: HomeAssistant,
) -> None:
    ambilight, coordinator, calls = await _ambilight(hass)
    coordinator.update()  # first evaluation sends the color for 60 °C
    await hass.async_block_till_done()
    assert len(calls) == 1
    ambilight._last_apply_ts -= 60  # a minute passes
    ambilight._last_ref_temp = None  # let the next update past the throttle
    coordinator.update()  # same temperature: same color, nothing is sent
    await hass.async_block_till_done()
    assert len(calls) == 1

    hass.states.async_set(LIGHT, "on", {"hs_color": [240.0, 100.0]})  # the user
    await hass.async_block_till_done()
    assert ambilight.manual_override, "the user's color was taken for an echo"
    ambilight.async_teardown()


async def test_session_end_leaves_the_lights_alone_when_switched_off(
    hass: HomeAssistant,
) -> None:
    ambilight, _, calls = await _ambilight(hass, enabled=False)
    hass.bus.async_fire(EVENT_SESSION_END, {"device_id": "d"})
    await hass.async_block_till_done()
    assert calls == []
    ambilight.async_teardown()


async def test_session_end_restores_the_standard_when_on(hass: HomeAssistant) -> None:
    ambilight, _, calls = await _ambilight(hass, enabled=True)
    hass.bus.async_fire(EVENT_SESSION_END, {"device_id": "d"})
    await hass.async_block_till_done()
    assert len(calls) == 1 and "color_temp_kelvin" in calls[0].data
    ambilight.async_teardown()


async def test_color_is_held_while_the_external_sensor_is_briefly_gone(
    hass: HomeAssistant,
) -> None:
    ambilight, coordinator, calls = await _ambilight(hass)
    coordinator.data.devices["d"]._ext_sensor_holding = True
    coordinator.update()
    await hass.async_block_till_done()
    assert calls == [], "painted from the internal sensor during the gap"
    ambilight.async_teardown()
