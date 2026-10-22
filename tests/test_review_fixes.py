"""Smaller fixes from the code review of 22 Oct 2026."""
import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.harvia_sauna.const import (
    API_PROVIDER_HARVIAIO,
    API_PROVIDER_MYHARVIA,
    CONF_API_PROVIDER,
    CONF_HEATER_MODEL,
    CONF_HEATER_POWER,
    DOMAIN,
)
from custom_components.harvia_sauna.coordinator import (
    HarviaDeviceData,
    _apply_state_data,
)
from custom_components.harvia_sauna.websocket_harviaio import HarviaIoWebSocketManager

from .test_setup import DEVICE_ID, FakeApi, _setup


async def test_unsupported_fenix_command_is_an_error(hass: HomeAssistant) -> None:
    """Auto light on a Fenix: no command exists; the switch must not flip."""
    _, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    api.async_request_state_change = AsyncMock(
        return_value={"handled": False, "reason": "unsupported_payload"}
    )
    before = hass.states.get("switch.sauna_auto_light").state
    service = "turn_off" if before == "on" else "turn_on"
    with pytest.raises(HomeAssistantError, match="autoLight"):
        await hass.services.async_call(
            "switch", service, {"entity_id": "switch.sauna_auto_light"}, blocking=True
        )
    assert hass.states.get("switch.sauna_auto_light").state == before


async def test_poll_keeps_a_state_push_that_arrived_meanwhile(hass: HomeAssistant) -> None:
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    manager = HarviaIoWebSocketManager(api, coordinator._async_handle_ws_update)
    real = api.async_get_latest_device_data

    async def telemetry_while_the_heater_starts(device_id):
        # The poll fetched the state (off) and now waits for telemetry; the
        # ignition message arrives by push in between.
        await manager._handle_message("device", {"payload": {"data": {
            "devicesStatesUpdateFeed": {"item": {
                "deviceId": DEVICE_ID,
                "reported": json.dumps({"heater": {"on": 1}, "saunaStatus": 1}),
            }}}}})
        return await real(device_id)

    api.async_get_latest_device_data = telemetry_while_the_heater_starts
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data.devices[DEVICE_ID].active, "the older polled state won"


def test_fenix_state_signal_strength_is_used() -> None:
    device = HarviaDeviceData(device_id="d")
    _apply_state_data(device, {"wifiRSSI": -61})
    assert device.wifi_rssi == -61


@pytest.mark.parametrize(("minutes", "sent"), [(90, 60), (30, 60), (180, 180)])
async def test_set_session_rounds_to_whole_hours(
    hass: HomeAssistant, minutes: int, sent: int
) -> None:
    _, api = await _setup(hass, API_PROVIDER_MYHARVIA)
    await hass.services.async_call(
        DOMAIN, "set_session", {"device_id": DEVICE_ID, "duration": minutes},
        blocking=True,
    )
    assert api.writes[-1] == (DEVICE_ID, {"onTime": sent})


async def test_measurement_free_frames_do_not_move_the_telemetry_clock(
    hass: HomeAssistant,
) -> None:
    """The Fenix "ESP" sub-feed (heapSize, rssi) carries no measurement."""
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    manager = HarviaIoWebSocketManager(api, coordinator._async_handle_ws_update)

    async def frame(sub_id: str, timestamp: str, data: dict) -> None:
        await manager._handle_message("data", {"payload": {"data": {
            "devicesMeasurementsUpdateFeed": {"item": {
                "deviceId": DEVICE_ID, "subId": sub_id, "timestamp": timestamp,
                "data": json.dumps(data),
            }}}}})

    await frame("C1", "1789125424874", {"temp": 60})
    await frame("ESP", "1789125500000", {"heapSize": 2000000, "rssi": -59})
    device = coordinator.data.devices[DEVICE_ID]
    assert device.timestamp == "1789125424874"

    # A cabin frame the push missed is still taken from the next poll
    api.telemetry_overrides = {"temperature": 61, "timestamp": "1789125454874"}
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert device.current_temp == 61


@pytest.mark.parametrize("provider", [API_PROVIDER_MYHARVIA, API_PROVIDER_HARVIAIO])
async def test_push_start_failure_retries_setup(hass: HomeAssistant, provider: str) -> None:
    """It used to end in setup_error, which Home Assistant never retries."""
    api = FakeApi(provider)
    api.async_start_push_updates = AsyncMock(side_effect=TimeoutError("user data"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret",
            CONF_API_PROVIDER: provider, CONF_HEATER_MODEL: "other",
            CONF_HEATER_POWER: "10.8",
        },
        unique_id="user@example.com",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.harvia_sauna.create_api_client", return_value=api):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_fenix_receiver_failure_propagates() -> None:
    """Logging and returning left push off for the whole runtime."""
    api = AsyncMock()
    api.async_get_receiver_id.side_effect = TimeoutError("devices")
    manager = HarviaIoWebSocketManager(api, AsyncMock())
    with pytest.raises(TimeoutError):
        await manager.async_start()
    assert not manager._running


async def test_xenio_user_data_failure_leaves_push_stopped() -> None:
    from custom_components.harvia_sauna.websocket import HarviaWebSocketManager

    api = AsyncMock()
    api.async_get_user_data.side_effect = TimeoutError("user data")
    manager = HarviaWebSocketManager(api, AsyncMock())
    with pytest.raises(TimeoutError):
        await manager.async_start()
    assert not manager._running, "a retry would return at once as 'already running'"


def test_a_frame_without_timestamp_keeps_the_last_one() -> None:
    from custom_components.harvia_sauna.coordinator import _apply_telemetry_data

    device = HarviaDeviceData(device_id="d")
    _apply_telemetry_data(device, {"temperature": 50, "timestamp": "1789125424874"})
    _apply_telemetry_data(device, {"temperature": 51, "timestamp": None})
    assert device.timestamp == "1789125424874"


async def test_fenix_account_without_devices_does_not_loop_setup() -> None:
    api = AsyncMock()
    api.async_get_receiver_id.return_value = None
    manager = HarviaIoWebSocketManager(api, AsyncMock())
    await manager.async_start()
    assert not manager._running and not manager._connections


async def test_refused_login_at_push_start_asks_for_reauth(hass: HomeAssistant) -> None:
    from custom_components.harvia_sauna.errors import HarviaAuthError

    api = FakeApi(API_PROVIDER_MYHARVIA)
    api.async_start_push_updates = AsyncMock(side_effect=HarviaAuthError("refused"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret",
            CONF_API_PROVIDER: API_PROVIDER_MYHARVIA, CONF_HEATER_MODEL: "other",
            CONF_HEATER_POWER: "10.8",
        },
        unique_id="user@example.com",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.harvia_sauna.create_api_client", return_value=api):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"].get("source") == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )
