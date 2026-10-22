"""A running session must not cancel tomorrow's preheat (code review, 22 Oct 2026).

Up to 2.11.0b5 _recompute() absorbed the schedule whenever the heater was on,
on every run - right after it was set and every 15 minutes - so "Next
session 07:00" set during an evening session was gone at once, silently.
"""
import datetime as dt

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.harvia_sauna.const import API_PROVIDER_MYHARVIA, DOMAIN

from .test_setup import DEVICE_ID, _setup


async def _scheduler(hass: HomeAssistant):
    entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = coordinator.data.devices[DEVICE_ID]
    device.current_temp = 22
    return coordinator.preheat, device


async def test_evening_session_keeps_tomorrows_schedule(hass: HomeAssistant) -> None:
    scheduler, device = await _scheduler(hass)
    device.active = True  # this evening's session is running
    ready_at = dt_util.utcnow() + dt.timedelta(hours=12)
    await scheduler.async_set_schedule(ready_at, 80, DEVICE_ID)
    await hass.async_block_till_done()
    assert scheduler.ready_at == ready_at, "the morning schedule was cancelled"

    scheduler._recompute()  # the 15-minute tick, sauna still on
    await hass.async_block_till_done()
    assert scheduler.ready_at == ready_at


async def test_heating_when_the_start_is_due_fulfils_the_schedule(
    hass: HomeAssistant,
) -> None:
    scheduler, device = await _scheduler(hass)
    device.active = True  # started by hand shortly before the planned start
    await scheduler.async_set_schedule(
        dt_util.utcnow() + dt.timedelta(minutes=10), 80, DEVICE_ID
    )
    await hass.async_block_till_done()
    assert scheduler.ready_at is None, "a due schedule with the heater on is fulfilled"


async def test_idle_sauna_arms_a_due_start(hass: HomeAssistant) -> None:
    scheduler, device = await _scheduler(hass)
    device.active = False
    ready_at = dt_util.utcnow() + dt.timedelta(minutes=10)
    await scheduler.async_set_schedule(ready_at, 80, DEVICE_ID)
    await hass.async_block_till_done()
    assert scheduler.ready_at == ready_at
    assert scheduler._unsub_start is not None, "the start must be armed"
    scheduler.async_teardown()
