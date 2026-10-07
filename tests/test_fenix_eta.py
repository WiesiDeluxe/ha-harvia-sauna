"""Fenix's timeToTarget is a live ETA, unlike Xenio's scheduled heatUpTime."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.harvia_sauna.api_harviaio import (
    HarviaIoApiClient,
    _normalize_telemetry_payload,
)
from custom_components.harvia_sauna.const import API_PROVIDER_HARVIAIO, READY_MODE_FIXED
from custom_components.harvia_sauna.coordinator import (
    DEVICE_STALE_TIMEOUT,
    HarviaDeviceData,
    SessionOptions,
    _apply_state_data,
    _apply_telemetry_data,
    _update_ref_trend_and_eta,
)
from custom_components.harvia_sauna.websocket_harviaio import HarviaIoWebSocketManager

from .test_setup import DEVICE_ID, _setup


def _heating_device():
    device = HarviaDeviceData(device_id="d", current_temp=47, target_temp=80)
    _apply_state_data(device, {"active": 1, "saunaStatus": 1})
    device._session_active = True
    return device


def _update(device, value):
    _apply_telemetry_data(
        device, _normalize_telemetry_payload({"data": {"timeToTarget": value}})
    )
    _update_ref_trend_and_eta(device, SessionOptions(), None)


def test_native_eta_updates_without_a_temperature_trend():
    device = _heating_device()
    for minutes in (38, 24, 23, 0):
        _update(device, minutes)
        assert device.time_to_ready_min == minutes
        seconds = (device.ready_at - datetime.now(timezone.utc)).total_seconds()
        assert abs(seconds - minutes * 60) < 2
    assert not device.ready, "ETA zero must not fire the temperature-based ready flag"
    assert device.heat_up_time == 0, "Xenio scheduling field must stay independent"


@pytest.mark.parametrize("value", [None, -1, "23", True, {}, float("nan"), float("inf")])
def test_invalid_eta_clears_previous_estimate(value):
    device = _heating_device()
    _update(device, 23)
    _update(device, value)
    assert device.time_to_target_min is None
    assert device.time_to_ready_min is None  # no fallback trend yet
    assert device.ready_at is None


def test_partial_updates_do_not_refresh_the_native_eta_age():
    device = _heating_device()
    with patch("time.monotonic", return_value=1000):
        _update(device, 23)
    ready_at = device.ready_at
    with patch("time.monotonic", return_value=1030):
        _apply_telemetry_data(device, {"humidity": 30})
        _update_ref_trend_and_eta(device, SessionOptions(), None)
    assert device.time_to_ready_min == 23
    assert abs((device.ready_at - ready_at).total_seconds() + 30) < 2
    with patch("time.monotonic", return_value=1000 + DEVICE_STALE_TIMEOUT):
        _apply_state_data(device, {"light": 1})
        _update_ref_trend_and_eta(device, SessionOptions(), None)
    assert device.time_to_ready_min is None


@pytest.mark.parametrize("state", [
    {"active": 0},
    {"active": 1, "saunaStatus": 5},
    {"activeProfile": 1},
    {"targetTemp": 90},
])
def test_changed_context_discards_previous_eta(state):
    device = _heating_device()
    _update(device, 23)
    _apply_state_data(device, state)
    assert device.time_to_target_min is None
    _apply_state_data(device, {"active": 1, "saunaStatus": 1})
    _update_ref_trend_and_eta(device, SessionOptions(), None)
    assert device.time_to_ready_min is None


def test_telemetry_target_change_without_eta_discards_previous_estimate():
    device = _heating_device()
    _update(device, 23)
    _apply_telemetry_data(device, {"targetTemp": 90})
    _update_ref_trend_and_eta(device, SessionOptions(), None)
    assert device.time_to_ready_min is None


def test_idle_telemetry_cannot_seed_the_next_session():
    device = HarviaDeviceData(device_id="d", current_temp=47, target_temp=80)
    _update(device, 23)
    _apply_state_data(device, {"active": 1})
    device._session_active = True
    _update_ref_trend_and_eta(device, SessionOptions(), None)
    assert device.time_to_ready_min is None


@pytest.mark.parametrize("options,external", [
    (SessionOptions(ready_mode=READY_MODE_FIXED, ready_fixed_temp=70), None),
    (SessionOptions(ext_sensor="sensor.reference"), 47),
    (SessionOptions(ext_sensor="sensor.reference"), None),
])
def test_custom_reference_keeps_the_trend_estimate(options, external):
    device = _heating_device()
    _update(device, 23)
    _update_ref_trend_and_eta(device, options, external)
    assert device.time_to_ready_min is None  # no usable trend yet


def test_xenio_heat_up_time_is_not_a_live_eta():
    device = _heating_device()
    _apply_state_data(device, {"heatUpTime": 60})
    _update_ref_trend_and_eta(device, SessionOptions(), None)
    assert device.heat_up_time == 60
    assert device.time_to_ready_min is None


@pytest.mark.parametrize("ready,cooldown", [(True, False), (False, True)])
def test_native_eta_does_not_override_ready_or_cooldown(ready, cooldown):
    device = _heating_device()
    _update(device, 23)
    device.ready = ready
    device._cooldown_active = cooldown
    device.time_to_ready_min = 0 if ready else None
    device.ready_at = None
    _update_ref_trend_and_eta(device, SessionOptions(), None)
    assert device.time_to_ready_min == (0 if ready else None)
    assert device.ready_at is None


def test_native_eta_falls_back_to_existing_temperature_trend():
    device = _heating_device()
    with patch("time.monotonic", return_value=1000):
        _update(device, 23)
    with patch("time.monotonic", return_value=1060):
        _apply_telemetry_data(device, {"temperature": 48, "timeToTarget": None})
        _update_ref_trend_and_eta(device, SessionOptions(), None)
    assert device.time_to_ready_min == 32


async def test_rest_telemetry_normalizes_native_eta():
    api = HarviaIoApiClient(None, "user@example.com", "unused")
    api._async_rest_request = AsyncMock(return_value={"data": {"timeToTarget": 38}})
    telemetry = await api.async_get_latest_device_data("d")
    assert telemetry["timeToTarget"] == 38


async def test_fenix_websocket_eta_reaches_existing_entities(hass):
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data["harvia_sauna"][entry.entry_id]
    api.state_overrides = {"active": 1, "saunaStatus": 1}
    api.telemetry_overrides = {"temperature": 47, "targetTemp": 80, "timeToTarget": 38}
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    device = coordinator.data.devices[DEVICE_ID]
    assert device.time_to_ready_min == 38

    manager = HarviaIoWebSocketManager(api, coordinator._async_handle_ws_update)
    for minutes in (24, 23):
        await manager._handle_message("data", {"payload": {"data": {
            "devicesMeasurementsUpdateFeed": {"item": {
                "deviceId": DEVICE_ID,
                "data": json.dumps({"temp": 62, "targetTemp": 80, "timeToTarget": minutes}),
            }},
        }}})
        await hass.async_block_till_done()
        assert device.time_to_ready_min == minutes
        from homeassistant.helpers import entity_registry as er
        entity_id = er.async_get(hass).async_get_entity_id(
            "sensor", "harvia_sauna", f"{DEVICE_ID}_time_to_ready"
        )
        assert float(hass.states.get(entity_id).state) == minutes
