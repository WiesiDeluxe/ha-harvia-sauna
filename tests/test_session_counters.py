"""Session counters across midnight, a new week and restarts (code review,
22 Oct 2026): after an overnight restart "Sessions today" restored
yesterday's count, "Sessions this week" kept last week's total until the
first session of the new week ended, and both used the host's clock instead
of Home Assistant's time zone."""
import datetime as dt

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.harvia_sauna.const import API_PROVIDER_MYHARVIA, DOMAIN
from custom_components.harvia_sauna.coordinator import local_day, local_week

from .test_setup import DEVICE_ID, _setup

TODAY_ENTITY = "sensor.sauna_sessions_today"


async def _restore_sessions_today(hass: HomeAssistant, changed: dt.datetime) -> str:
    mock_restore_cache(hass, [State(TODAY_ENTITY, "3", last_changed=changed)])
    await _setup(hass, API_PROVIDER_MYHARVIA)
    return hass.states.get(TODAY_ENTITY).state


async def test_yesterdays_count_is_not_restored(hass: HomeAssistant) -> None:
    yesterday = dt_util.now() - dt.timedelta(days=1)
    assert await _restore_sessions_today(hass, yesterday) == "0"


async def test_todays_count_survives_a_restart(hass: HomeAssistant) -> None:
    assert await _restore_sessions_today(hass, dt_util.utcnow()) == "3"


async def test_weekly_count_resets_when_the_week_changes(hass: HomeAssistant) -> None:
    entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = coordinator.data.devices[DEVICE_ID]
    device.sessions_week = 5
    device._sessions_week_anchor = "2026-42"  # last week, no session since
    coordinator._run_session_tracking(device)
    assert device.sessions_week == 0
    assert device._sessions_week_anchor == local_week()


async def test_dates_follow_home_assistants_time_zone(
    hass: HomeAssistant, freezer
) -> None:
    """06:00 UTC is still the previous evening in the tests' US/Pacific."""
    await hass.config.async_set_time_zone("US/Pacific")
    freezer.move_to("2026-10-26T06:00:00+00:00")  # Monday 06:00 UTC
    assert local_day() == "2026-10-25"  # Sunday 23:00 in Pacific
    assert local_week() == "2026-43"
