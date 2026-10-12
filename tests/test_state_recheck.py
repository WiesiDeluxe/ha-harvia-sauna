"""A state message lost at ignition must not leave the sauna "off" (#14).

Reported by @fasdav on a Fenix (2.11.0b3, start at the panel): switch and
thermostat showed off for 42 min of heat-up while telemetry reported heatOn;
they switched on only when the target was reached, and the session was
recorded as 24 instead of 66 min. Every push reset the 5-minute fallback poll
timer, and with a telemetry frame every ~30 s the poll never ran.
"""
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.harvia_sauna.const import (
    API_PROVIDER_HARVIAIO,
    DOMAIN,
    POLL_FAILURES_BEFORE_UNAVAILABLE,
    SCAN_INTERVAL_FALLBACK,
    STATE_RECHECK_DELAY,
)
from custom_components.harvia_sauna.coordinator import _is_stale_telemetry
from custom_components.harvia_sauna.errors import HarviaConnectionError
from custom_components.harvia_sauna.websocket_harviaio import HarviaIoWebSocketManager

from .test_setup import DEVICE_ID, _setup

CLIMATE = "climate.sauna_thermostat"


def _frame(data: dict, timestamp: str = "1789125424874") -> tuple[str, dict]:
    return "data", {"payload": {"data": {"devicesMeasurementsUpdateFeed": {"item": {
        "deviceId": DEVICE_ID, "timestamp": timestamp, "data": json.dumps(data),
    }}}}}


def _state(reported: dict) -> tuple[str, dict]:
    return "device", {"payload": {"data": {"devicesStatesUpdateFeed": {"item": {
        "deviceId": DEVICE_ID, "reported": json.dumps(reported),
    }}}}}


async def _fenix(hass: HomeAssistant):
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    manager = HarviaIoWebSocketManager(api, coordinator._async_handle_ws_update)
    reads = 0
    real = api.async_get_device_state

    async def counting(device_id):
        nonlocal reads
        reads += 1
        return await real(device_id)

    api.async_get_device_state = counting
    return entry, api, coordinator, manager, lambda: reads


async def _push(hass: HomeAssistant, manager, message) -> None:
    await manager._handle_message(*message)
    await hass.async_block_till_done(wait_background_tasks=True)


HEATING = {"temp": 27, "heatOn": 1, "targetTemp": 95, "timeToTarget": 42}


async def test_lost_ignition_message_is_corrected(hass: HomeAssistant) -> None:
    """Started at the panel; only telemetry arrives. The state is re-read."""
    _, api, coordinator, manager, reads = await _fenix(hass)
    device = coordinator.data.devices[DEVICE_ID]
    api.state_overrides = {"active": 1, "saunaStatus": 1}  # what the cloud holds

    await _push(hass, manager, _frame(HEATING))
    assert hass.states.get(CLIMATE).state == "off" and reads() == 0

    device._heat_without_active_since -= STATE_RECHECK_DELAY  # a minute passes
    await _push(hass, manager, _frame(HEATING))
    assert reads() == 1, "the state must be re-read"
    assert hass.states.get(CLIMATE).state == "heat"
    assert coordinator._state_rechecks == 1
    await _push(hass, manager, _frame(HEATING))
    assert device._heat_without_active_since is None, "consistent again"


async def test_a_mismatch_shorter_than_the_delay_does_not_recheck(
    hass: HomeAssistant,
) -> None:
    """The state message may trail telemetry by a frame or two."""
    _, _, coordinator, manager, reads = await _fenix(hass)
    device = coordinator.data.devices[DEVICE_ID]
    await _push(hass, manager, _frame(HEATING))
    device._heat_without_active_since -= STATE_RECHECK_DELAY - 2
    await _push(hass, manager, _frame(HEATING))
    assert reads() == 0 and coordinator._state_rechecks == 0


async def test_normal_ignition_order_does_not_recheck(hass: HomeAssistant) -> None:
    """Telemetry first, the state message seconds later: no extra read."""
    _, _, coordinator, manager, reads = await _fenix(hass)
    await _push(hass, manager, _frame(HEATING))
    await _push(hass, manager, _state({"heater": {"on": 1}, "saunaStatus": 1}))
    await _push(hass, manager, _frame(HEATING))
    device = coordinator.data.devices[DEVICE_ID]
    assert device._heat_without_active_since is None
    assert reads() == 0
    assert hass.states.get(CLIMATE).state == "heat"


async def test_recheck_is_rate_limited(hass: HomeAssistant) -> None:
    """If the cloud's state also says off, re-read once per scan interval."""
    _, _, coordinator, manager, reads = await _fenix(hass)
    device = coordinator.data.devices[DEVICE_ID]
    await _push(hass, manager, _frame(HEATING))
    device._heat_without_active_since -= STATE_RECHECK_DELAY
    await _push(hass, manager, _frame(HEATING))
    assert reads() == 1
    for _ in range(3):
        await _push(hass, manager, _frame(HEATING))
    assert reads() == 1, "re-read again before the scan interval passed"
    assert coordinator._state_rechecks == 1
    device._state_recheck_ts -= SCAN_INTERVAL_FALLBACK
    await _push(hass, manager, _frame(HEATING))
    # HA's request debouncer delays the read itself (10 s cooldown)
    assert coordinator._state_rechecks == 2


async def test_scheduled_wait_does_not_recheck(hass: HomeAssistant) -> None:
    """Waiting for a schedule reports heatOn 1 with saunaStatus 5 (#9)."""
    _, _, coordinator, manager, reads = await _fenix(hass)
    device = coordinator.data.devices[DEVICE_ID]
    await _push(hass, manager, _state({"heater": {"on": 1}, "saunaStatus": 5}))
    await _push(hass, manager, _frame(HEATING))
    assert device._heat_without_active_since is None
    await _push(hass, manager, _frame(HEATING))
    assert reads() == 0


async def test_pushes_do_not_postpone_the_fallback_poll(
    hass: HomeAssistant, freezer
) -> None:
    """Seven minutes of telemetry every 30 s: the 5-minute poll still runs."""
    _, _, _, manager, reads = await _fenix(hass)
    await _push(hass, manager, _state({"heater": {"on": 1}, "saunaStatus": 1}))
    for _ in range(14):
        freezer.tick(timedelta(seconds=30))
        await _push(hass, manager, _frame({**HEATING, "temp": 40}))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert reads() >= 1, "the fallback poll never ran while pushes arrived"


async def test_a_push_still_restores_entities_after_failed_polls(
    hass: HomeAssistant,
) -> None:
    _, api, coordinator, manager, _ = await _fenix(hass)
    api.async_get_device_state = AsyncMock(side_effect=HarviaConnectionError("down"))
    for _ in range(POLL_FAILURES_BEFORE_UNAVAILABLE):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE
    await _push(hass, manager, _frame({"temp": 30, "heatOn": 0}))
    assert coordinator.last_update_success
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


@pytest.mark.parametrize(("new", "last", "stale"), [
    ("1789125424874", "1789125424874", True),   # the frame a push delivered
    ("1789125394874", "1789125424874", True),   # older by one frame
    ("1789125454874", "1789125424874", False),  # newer
    ("1789125454874", None, False),             # nothing applied yet
    (None, "1789125424874", False),             # poll without a timestamp
    ("1789125454", "1789125424874", False),     # seconds vs ms: do not guess
    ("2026-10-11T10:00:00Z", "1789125424874", False),
])
def test_stale_telemetry_rule(new, last, stale) -> None:
    assert _is_stale_telemetry(new, last) is stale


async def test_poll_does_not_reapply_the_pushed_frame(hass: HomeAssistant) -> None:
    """Re-applying it would restart the age of the native ETA (#13)."""
    _, api, coordinator, manager, _ = await _fenix(hass)
    device = coordinator.data.devices[DEVICE_ID]
    await _push(hass, manager, _state({"heater": {"on": 1}, "saunaStatus": 1}))
    await _push(hass, manager, _frame(HEATING, "1789125424874"))
    device._time_to_target_ts -= 100
    stamp = device._time_to_target_ts

    api.telemetry_overrides = {**HEATING, "timestamp": "1789125424874"}
    api.state_overrides = {"active": 1, "saunaStatus": 1}
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert device._time_to_target_ts == stamp, "the same frame was applied again"

    api.telemetry_overrides = {**HEATING, "timeToTarget": 40, "timestamp": "1789125454874"}
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert device.time_to_target_min == 40, "a newer polled frame must apply"
