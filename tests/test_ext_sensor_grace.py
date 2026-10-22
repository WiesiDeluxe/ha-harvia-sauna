"""A briefly missing external reference sensor must not hand decisions to the
internal sensor (code review, 22 Oct 2026).

COOLDOWN_EXT_SENSOR_GRACE_SEC only gated a log line up to 2.11.0b5: as soon as
the BLE sensor went unavailable every caller used the internal Harvia sensor.
Reproduced: cooldown to 60 °C, external sensor at 80, BLE gap while the
internal sensor reads 55 - three evaluations ended the session.
"""
import logging

from homeassistant.core import HomeAssistant

from custom_components.harvia_sauna.const import (
    COOLDOWN_EXT_SENSOR_GRACE_SEC,
    DOMAIN,
    EVENT_SESSION_END,
)

from .test_availability_and_unload import REF, _setup_with_cooldown


async def _cooldown(hass: HomeAssistant):
    """A session that just went into cooldown, reference sensor at 80 °C."""
    entry, _ = await _setup_with_cooldown(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = next(iter(coordinator.data.devices.values()))
    ended = []
    hass.bus.async_listen(EVENT_SESSION_END, ended.append)
    device.active = True
    device.target_temp = 80
    device.current_temp = 90
    coordinator._run_session_tracking(device)  # session starts
    device._session_start_time -= 3600  # long enough to count
    device.active = False
    coordinator._run_session_tracking(device)  # heater off -> cooldown
    assert device._cooldown_active
    return coordinator, device, ended


def _frame(device, n: int, temp: float) -> None:
    """A new telemetry frame from the heater's own sensor."""
    device.timestamp = str(1789125424874 + n * 30000)
    device.current_temp = temp


async def test_gap_does_not_end_cooldown_on_the_internal_sensor(
    hass: HomeAssistant,
) -> None:
    coordinator, device, ended = await _cooldown(hass)
    hass.states.async_set(REF, "unavailable")
    for n in range(5):
        _frame(device, n, 55)  # the heater's own sensor is below 60
        coordinator._run_session_tracking(device)
    assert device._ext_sensor_holding
    assert device._session_active and not ended
    assert device._cooldown_below_count == 0


async def test_gap_does_not_reset_the_confirmation(hass: HomeAssistant) -> None:
    coordinator, device, ended = await _cooldown(hass)
    for value in ("59", "58"):  # two readings below 60
        hass.states.async_set(REF, value)
        coordinator._run_session_tracking(device)
    assert device._cooldown_below_count == 2
    hass.states.async_set(REF, "unavailable")
    _frame(device, 1, 90)  # internal sensor still hot
    coordinator._run_session_tracking(device)
    assert device._cooldown_below_count == 2, "the gap reset the confirmation"
    hass.states.async_set(REF, "57")
    coordinator._run_session_tracking(device)
    assert ended, "the third reading below 60 must end the session"


async def test_after_the_grace_period_the_internal_sensor_takes_over(
    hass: HomeAssistant, caplog
) -> None:
    coordinator, device, ended = await _cooldown(hass)
    hass.states.async_set(REF, "unavailable")
    coordinator._run_session_tracking(device)
    device._ext_sensor_invalid_since -= COOLDOWN_EXT_SENSOR_GRACE_SEC + 1
    with caplog.at_level(logging.WARNING):
        for n in range(3):
            _frame(device, n, 55)
            coordinator._run_session_tracking(device)
    assert not device._ext_sensor_holding
    assert ended
    assert caplog.text.count("falling back to the internal Harvia sensor") == 1


async def test_heater_off_during_a_gap_starts_the_cooldown(hass: HomeAssistant) -> None:
    """Up to b5 a missing reference at heater-off ended the session at once."""
    entry, _ = await _setup_with_cooldown(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = next(iter(coordinator.data.devices.values()))
    device.active, device.target_temp, device.current_temp = True, 80, 90
    coordinator._run_session_tracking(device)
    device._session_start_time -= 3600
    hass.states.async_set(REF, "unavailable")
    device.current_temp = None  # nothing usable at all, only the gap
    device.active = False
    coordinator._run_session_tracking(device)
    assert device._session_active and device._cooldown_active


async def test_max_temperature_waits_for_the_external_sensor(hass: HomeAssistant) -> None:
    entry, _ = await _setup_with_cooldown(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.session_options.ext_sensor_for_max_temp = True
    device = next(iter(coordinator.data.devices.values()))
    hass.states.async_set(REF, "unavailable")
    device.active, device.target_temp, device.current_temp = True, 80, 104
    coordinator._run_session_tracking(device)  # starts during the gap
    coordinator._run_session_tracking(device)
    assert device._session_max_temp == 0.0, "internal 104 °C taken as the max"
    hass.states.async_set(REF, "78")
    coordinator._run_session_tracking(device)
    assert device._session_max_temp == 78


async def _heating_with_gap(hass: HomeAssistant):
    """A session heating up while the BLE sensor is briefly gone."""
    entry, _ = await _setup_with_cooldown(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = next(iter(coordinator.data.devices.values()))
    device.active, device.target_temp, device.current_temp = True, 80, 60
    hass.states.async_set(REF, "55")
    coordinator._run_session_tracking(device)
    hass.states.async_set(REF, "unavailable")
    return coordinator, device


async def test_ready_is_not_taken_from_the_internal_sensor_during_a_gap(
    hass: HomeAssistant,
) -> None:
    coordinator, device = await _heating_with_gap(hass)
    _frame(device, 1, 85)  # the heater's own sensor is above the target
    coordinator._run_session_tracking(device)
    assert device._ext_sensor_holding and not device.ready


async def test_heating_model_learns_nothing_during_a_gap(hass: HomeAssistant) -> None:
    from unittest.mock import MagicMock

    coordinator, device = await _heating_with_gap(hass)
    coordinator.heating_model = MagicMock()
    _frame(device, 1, 85)
    coordinator._run_session_tracking(device)
    coordinator.heating_model.add_sample.assert_not_called()


async def test_a_new_gap_gets_its_own_grace_period(hass: HomeAssistant) -> None:
    coordinator, device, _ = await _cooldown(hass)
    hass.states.async_set(REF, "unavailable")
    coordinator._run_session_tracking(device)
    device._ext_sensor_invalid_since -= COOLDOWN_EXT_SENSOR_GRACE_SEC + 1
    coordinator._run_session_tracking(device)
    assert not device._ext_sensor_holding  # fell back after the first gap
    hass.states.async_set(REF, "70")  # back
    coordinator._run_session_tracking(device)
    hass.states.async_set(REF, "unavailable")  # gone again, just now
    coordinator._run_session_tracking(device)
    assert device._ext_sensor_holding, "the old gap's start was kept"


async def _preheat_with_plan(hass: HomeAssistant, hours: float):
    import datetime as dt

    from homeassistant.util import dt as dt_util

    entry, _ = await _setup_with_cooldown(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = next(iter(coordinator.data.devices.values()))
    device.active, device.current_temp = False, 22
    hass.states.async_set(REF, "22")
    scheduler = coordinator.preheat
    await scheduler.async_set_schedule(
        dt_util.utcnow() + dt.timedelta(hours=hours), 80, device.device_id
    )
    return scheduler, device


async def test_preheat_keeps_its_plan_during_a_gap(hass: HomeAssistant) -> None:
    scheduler, device = await _preheat_with_plan(hass, 5)
    planned = scheduler.planned_start
    assert planned is not None
    hass.states.async_set(REF, "unavailable")
    device.current_temp = 60  # the heater's own sensor reads much warmer
    scheduler._recompute()
    assert scheduler.planned_start == planned, "re-planned from the internal sensor"
    scheduler.async_teardown()


async def test_preheat_arms_a_due_start_during_a_gap(hass: HomeAssistant) -> None:
    """Right after a restart the BLE sensor is often gone for minutes."""
    import datetime as dt

    from homeassistant.util import dt as dt_util

    scheduler, _ = await _preheat_with_plan(hass, 5)
    assert scheduler._unsub_start is None  # five hours away: not armed yet
    hass.states.async_set(REF, "unavailable")
    scheduler._planned_start = dt_util.utcnow() + dt.timedelta(minutes=5)  # now due
    scheduler._recompute()
    assert scheduler._unsub_start is not None, "a due start was not armed"
    scheduler.async_teardown()


async def test_preheat_without_a_plan_uses_the_internal_sensor_during_a_gap(
    hass: HomeAssistant,
) -> None:
    import datetime as dt

    from homeassistant.util import dt as dt_util

    entry, _ = await _setup_with_cooldown(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = next(iter(coordinator.data.devices.values()))
    device.active, device.current_temp = False, 22
    hass.states.async_set(REF, "unavailable")  # gone before anything was planned
    scheduler = coordinator.preheat
    await scheduler.async_set_schedule(
        dt_util.utcnow() + dt.timedelta(hours=5), 80, device.device_id
    )
    assert scheduler.planned_start is not None, "nothing planned at all"
    scheduler.async_teardown()
