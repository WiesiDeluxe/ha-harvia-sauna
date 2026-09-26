"""Entities flapping to unavailable during cooldown, and unloads that hang.

Both measured on the maintainer's Xenio on 2026-09-26. Every "unavailable"
came within a millisecond of the BLE reference sensor updating, three times
15 minutes apart, 4.5 minutes each; a manual reload then left the config
entry in unload_in_progress until Home Assistant was restarted.
"""
import asyncio
import contextlib
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.harvia_sauna.const import (
    API_PROVIDER_MYHARVIA,
    CONF_API_PROVIDER,
    CONF_HEATER_MODEL,
    CONF_HEATER_POWER,
    DOMAIN,
)

from .test_setup import FakeApi, _setup

REF = "sensor.ref_temperature"


async def _setup_with_cooldown(hass: HomeAssistant) -> tuple[MockConfigEntry, FakeApi]:
    api = FakeApi(API_PROVIDER_MYHARVIA)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_API_PROVIDER: API_PROVIDER_MYHARVIA,
            CONF_HEATER_MODEL: "other",
            CONF_HEATER_POWER: 10800,
        },
        options={
            "session_end_mode": "cooldown",
            "cooldown_temp_sensor": REF,
            "cooldown_end_mode": "fixed_temp",
            "cooldown_end_temp": 60,
        },
        unique_id="user@example.com",
    )
    entry.add_to_hass(hass)
    hass.states.async_set(REF, "80")
    with patch("custom_components.harvia_sauna.create_api_client", return_value=api):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, api


async def test_reference_sensor_ticks_do_not_starve_the_fallback_poll(
    hass: HomeAssistant, freezer
) -> None:
    """A reference sensor updating every minute must not cancel polling.

    The cooldown listener used async_set_updated_data(), which resets the
    5-minute poll timer. With a BLE sensor ticking every ~60 s the poll never
    ran, the device's last-update stamp came from its own pushes only (every
    ~15 min when idle) and after 10 min it counted as stale: every entity
    unavailable for ~4.5 of every 15 minutes, all through the cooldown.
    """
    entry, api = await _setup_with_cooldown(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = next(iter(coordinator.data.devices.values()))
    device._cooldown_active = True  # heater just went off, cooldown running

    polls = 0
    real = api.async_get_device_state

    async def counting(device_id):
        nonlocal polls
        polls += 1
        return await real(device_id)

    api.async_get_device_state = counting
    reschedules = 0
    real_schedule = coordinator._schedule_refresh

    def counting_schedule():
        nonlocal reschedules
        reschedules += 1
        real_schedule()

    coordinator._schedule_refresh = counting_schedule

    for minute in range(1, 8):  # seven minutes of a sensor ticking every 60 s
        freezer.tick(timedelta(seconds=60))
        hass.states.async_set(REF, str(80 - minute * 0.1))
        await hass.async_block_till_done()
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert polls >= 1, "the 5-minute fallback poll never ran during cooldown"
    # Only the poll itself may re-arm the timer; the sensor ticks must not.
    assert reschedules == polls, f"poll timer re-armed {reschedules}x for {polls} poll(s)"
    assert hass.states.get("climate.sauna_thermostat").state != "unavailable"


async def test_unload_completes_even_if_push_shutdown_hangs(hass: HomeAssistant) -> None:
    """A dead socket must not leave the entry in unload_in_progress forever."""
    entry, api = await _setup(hass, API_PROVIDER_MYHARVIA)

    async def never_returns():
        await asyncio.Event().wait()

    api.async_stop_push_updates = never_returns

    with patch("custom_components.harvia_sauna.coordinator.WS_STOP_TIMEOUT", 0.05):
        async with asyncio.timeout(5):
            assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


class _DeadSocket:
    """send()/close() block for good, like a connection whose peer is gone."""

    async def send(self, _msg):
        await asyncio.Event().wait()

    async def close(self):
        await asyncio.Event().wait()


@pytest.mark.parametrize("module", ["websocket", "websocket_harviaio"])
async def test_websocket_stop_gives_up_on_a_dead_socket(
    hass: HomeAssistant, module: str
) -> None:
    """send()/close() on a half-dead connection are bounded, per socket."""
    import importlib

    mod = importlib.import_module(f"custom_components.harvia_sauna.{module}")
    cls = mod.HarviaWebSocket if module == "websocket" else mod.HarviaIoWebSocket
    ws = cls.__new__(cls)
    ws._websocket = _DeadSocket()
    ws._subscription_id = "sub"
    ws._running = True

    with patch("custom_components.harvia_sauna.websocket.WS_STOP_TIMEOUT", 0.05):
        async with asyncio.timeout(2):
            await ws.async_stop()
    assert ws._websocket is None and ws._running is False


@pytest.mark.parametrize("module", ["websocket", "websocket_harviaio"])
async def test_manager_stop_abandons_a_task_that_ignores_cancellation(
    hass: HomeAssistant, module: str
) -> None:
    """The manager returns even if a run loop swallows its cancellation."""
    import importlib

    mod = importlib.import_module(f"custom_components.harvia_sauna.{module}")
    cls = mod.HarviaWebSocketManager if module == "websocket" else mod.HarviaIoWebSocketManager

    release = asyncio.Event()

    async def stubborn():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()  # ignores the cancellation, keeps running

    task = hass.loop.create_task(stubborn())
    await asyncio.sleep(0)  # let it reach its await, or cancel() never enters the loop
    manager = cls.__new__(cls)
    manager._connections = []
    manager._tasks = [task]
    manager._running = True

    try:
        with patch("custom_components.harvia_sauna.websocket.WS_STOP_TIMEOUT", 0.05):
            async with asyncio.timeout(2):
                await manager.async_stop()
        assert manager._tasks == []
    finally:
        # The abandoned task gets a second cancellation from the timed-out
        # gather(), so it ends cancelled once released - let it.
        release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _in_cooldown(coordinator) -> object:
    """Put the device where the heater just went off and cooldown runs."""
    import time

    device = next(iter(coordinator.data.devices.values()))
    device.active = False
    device._session_active = True
    device._session_start_time = time.monotonic() - 3600
    device._cooldown_active = True
    device._cooldown_started = time.monotonic()
    device._frozen_target_temp = 62.0
    device._cooldown_below_count = 0
    return device


async def test_cooldown_guard_counts_readings_not_evaluations(hass: HomeAssistant) -> None:
    """One reading below the end threshold, evaluated three times, is one.

    Session tracking runs from the sensor listener, every push and every
    poll. Counting evaluations let a single low outlier from a BLE sensor
    reach the three confirmations and end the session - more likely once the
    poll runs during cooldown again.
    """
    entry, _ = await _setup_with_cooldown(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = _in_cooldown(coordinator)

    hass.states.async_set(REF, "59.5")  # one outlier below the 60 °C end
    await hass.async_block_till_done()
    coordinator._run_session_tracking(device)  # the next poll
    coordinator._run_session_tracking(device)  # a push
    assert device._session_active, "one reading must not end the session"
    assert device._cooldown_below_count == 1


async def test_cooldown_still_ends_after_three_real_readings(hass: HomeAssistant) -> None:
    """The guard must not stop a genuine cooldown from ending."""
    entry, _ = await _setup_with_cooldown(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = _in_cooldown(coordinator)

    for value in ("59.5", "59.4"):
        hass.states.async_set(REF, value)
        await hass.async_block_till_done()
    assert device._session_active
    hass.states.async_set(REF, "59.3")
    await hass.async_block_till_done()
    assert not device._session_active, "three readings below 60 °C end it"


async def test_cooldown_counts_a_repeated_value_reported_again(hass: HomeAssistant) -> None:
    """A sensor re-reporting the same value is a new reading (last_reported)."""
    entry, _ = await _setup_with_cooldown(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = _in_cooldown(coordinator)

    for _ in range(3):
        hass.states.async_set(REF, "59.5", force_update=True)
        await hass.async_block_till_done()
        coordinator._run_session_tracking(device)
    assert not device._session_active


async def test_no_poll_while_the_unload_is_still_shutting_down(
    hass: HomeAssistant, freezer
) -> None:
    """Polling stops when shutdown begins, not when the entities are gone.

    Home Assistant cancels the poll once the last entity is removed, but the
    push shutdown runs before that. On 2026-09-26 a poll landed at 18:01
    while the entry was still unloading and brought the entities back into a
    half-torn-down integration. The base-class async_shutdown() - skipped by
    our override - is what stops the timer at that point.
    """
    entry, api = await _setup(hass, API_PROVIDER_MYHARVIA)
    release = asyncio.Event()

    async def slow_stop():
        await release.wait()

    api.async_stop_push_updates = slow_stop
    polls = 0

    async def counting(device_id):
        nonlocal polls
        polls += 1
        return {}

    api.async_get_device_state = counting

    unload = hass.async_create_task(hass.config_entries.async_unload(entry.entry_id))
    for _ in range(3):
        await asyncio.sleep(0)  # let the unload reach the push shutdown
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await asyncio.sleep(0)
    assert polls == 0, "polled while the entry was shutting down"

    release.set()
    assert await unload
