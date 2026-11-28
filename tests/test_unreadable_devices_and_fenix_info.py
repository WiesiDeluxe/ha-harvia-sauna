"""Devices without a cabin state, Fenix firmware and Fenix connection state.

#18: a MyHarvia sauna sensor has no cabin shadow "C1". The cloud answers
HTTP 404 "Shadow 'C1' for device '...' not found", which failed the whole
poll and kept the setup retrying.

#16: a Fenix reports its firmware as deviceFwVersion in the device-info
message of the state feed, and the cloud reports connectionState.connected
with the polled state and with the state feed. Neither was used.
"""
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.harvia_sauna.api_harviaio import (
    HarviaIoApiClient,
    _normalize_state_payload,
)
from custom_components.harvia_sauna.const import (
    API_PROVIDER_HARVIAIO,
    API_PROVIDER_MYHARVIA,
    CONF_API_PROVIDER,
    CONF_HEATER_MODEL,
    CONF_HEATER_POWER,
    DOMAIN,
)
from custom_components.harvia_sauna.coordinator import DEVICE_STALE_TIMEOUT
from custom_components.harvia_sauna.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.harvia_sauna.errors import (
    HarviaAuthError,
    HarviaConnectionError,
    HarviaNotFoundError,
)
from custom_components.harvia_sauna.websocket_harviaio import HarviaIoWebSocketManager

from .test_setup import DEVICE_ID, FakeApi, _setup

SENSOR_ID = "d364f3a1-23fd-4563-8b69-60584acf88ac"
CLIMATE = "climate.sauna_thermostat"
# The cloud's answer from #18, as the client turns it into an error
NO_CABIN = HarviaNotFoundError(
    'HTTP 404: {"error":"Not Found","message":"Shadow \'C1\' for device '
    f"'{SENSOR_ID}' not found\"}}"
)


class AccountApi(FakeApi):
    """A harvia.io account whose device list can hold a sauna sensor."""

    def __init__(self, device_ids: list[str]) -> None:
        super().__init__(API_PROVIDER_HARVIAIO)
        self.device_ids = device_ids
        self.no_cabin = {SENSOR_ID}
        self.ignored: list[str] = []
        self.probed: list[str] = []

    async def async_get_devices(self):
        return [
            {"device_id": i, "raw": {"deviceId": i, "type": "SaunaSensor"}}
            if i == SENSOR_ID
            else {"device_id": i, "raw": {"deviceId": i, "type": "Fenix"}}
            for i in self.device_ids
        ]

    async def async_get_device_state(self, device_id: str) -> dict:
        if device_id in self.no_cabin:
            raise NO_CABIN
        return await super().async_get_device_state(device_id)

    def ignore_device(self, device_id: str) -> None:
        self.ignored.append(device_id)

    async def async_probe_device(self, device_id: str) -> dict:
        self.probed.append(device_id)
        return {"state": {"state": {"temp": 21, "wifissid": "HomeNet"}}}


async def _setup_account(
    hass: HomeAssistant, device_ids: list[str]
) -> tuple[MockConfigEntry, AccountApi]:
    api = AccountApi(device_ids)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_API_PROVIDER: API_PROVIDER_HARVIAIO,
            CONF_HEATER_MODEL: "other",
            CONF_HEATER_POWER: "10.8",
        },
        options={},
        unique_id="user@example.com",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.harvia_sauna.create_api_client", return_value=api):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, api


async def _poll(hass: HomeAssistant, coordinator) -> None:
    await coordinator.async_refresh()
    await hass.async_block_till_done()


# --- #18: a device without a cabin state ---------------------------------


async def test_a_sensor_does_not_block_the_controller_next_to_it(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="custom_components.harvia_sauna"):
        entry, api = await _setup_account(hass, [SENSOR_ID, DEVICE_ID])
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await _poll(hass, coordinator)

    assert entry.state is ConfigEntryState.LOADED
    assert list(coordinator.data.devices) == [DEVICE_ID]
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

    noted = coordinator.unreadable_devices[SENSOR_ID]
    assert noted["type"] == "SaunaSensor"
    assert "Shadow 'C1'" in noted["error"]
    assert noted["list_entry"]["deviceId"] == SENSOR_ID
    assert noted["probe"]["state"]["state"]["temp"] == 21
    # Told to the API client (push receiver), probed and logged once only,
    # although the sensor is asked again with every poll.
    assert api.ignored == [SENSOR_ID]
    assert api.probed == [SENSOR_ID]
    warnings = [r for r in caplog.records if SENSOR_ID in r.getMessage()]
    assert len(warnings) == 1
    assert "SaunaSensor" in warnings[0].getMessage()


async def test_a_sensor_alone_no_longer_keeps_the_setup_retrying(
    hass: HomeAssistant,
) -> None:
    entry, _ = await _setup_account(hass, [SENSOR_ID])
    assert entry.state is ConfigEntryState.LOADED
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.data.devices == {}
    assert SENSOR_ID in coordinator.unreadable_devices
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_a_controller_read_before_is_not_dropped_on_a_404(
    hass: HomeAssistant,
) -> None:
    """Only a device never read is skipped; for a known one it is an outage."""
    entry, api = await _setup_account(hass, [DEVICE_ID])
    coordinator = hass.data[DOMAIN][entry.entry_id]
    api.no_cabin = {DEVICE_ID}
    await _poll(hass, coordinator)

    assert coordinator.unreadable_devices == {}
    assert api.ignored == []
    assert DEVICE_ID in coordinator.data.devices
    assert coordinator._poll_failures == 1
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


async def test_a_device_that_becomes_readable_is_added_by_a_reload(
    hass: HomeAssistant,
) -> None:
    entry, api = await _setup_account(hass, [SENSOR_ID, DEVICE_ID])
    coordinator = hass.data[DOMAIN][entry.entry_id]
    api.no_cabin = set()
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _poll(hass, coordinator)
        await _poll(hass, coordinator)
    reload.assert_called_once_with(entry.entry_id)
    assert coordinator.unreadable_devices == {}


async def test_two_devices_becoming_readable_reload_once(hass: HomeAssistant) -> None:
    other = "0f1e2d3c-0000-4000-8000-000000000001"
    api_ids = [SENSOR_ID, other, DEVICE_ID]
    with patch.object(AccountApi, "__init__", autospec=True) as init:
        def _init(self, device_ids):
            FakeApi.__init__(self, API_PROVIDER_HARVIAIO)
            self.device_ids = device_ids
            self.no_cabin = {SENSOR_ID, other}
            self.ignored, self.probed = [], []
        init.side_effect = _init
        entry, api = await _setup_account(hass, api_ids)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert set(coordinator.unreadable_devices) == {SENSOR_ID, other}
    api.no_cabin = set()
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _poll(hass, coordinator)
    reload.assert_called_once_with(entry.entry_id)


async def test_a_device_without_cabin_telemetry_is_skipped_too(
    hass: HomeAssistant,
) -> None:
    """State readable, telemetry 404: it must not fail the controller's poll."""
    entry, api = await _setup_account(hass, [DEVICE_ID])
    coordinator = hass.data[DOMAIN][entry.entry_id]
    api.device_ids = [DEVICE_ID, SENSOR_ID]
    api.no_cabin = set()
    real = api.async_get_latest_device_data

    async def telemetry(device_id):
        if device_id == SENSOR_ID:
            raise NO_CABIN
        return await real(device_id)

    api.async_get_latest_device_data = telemetry
    await _poll(hass, coordinator)
    assert coordinator._poll_failures == 0
    assert list(coordinator.data.devices) == [DEVICE_ID]
    assert SENSOR_ID in coordinator.unreadable_devices


async def test_a_failing_probe_does_not_fail_the_poll(hass: HomeAssistant) -> None:
    with patch.object(
        AccountApi, "async_probe_device", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        entry, _ = await _setup_account(hass, [SENSOR_ID, DEVICE_ID])
    assert entry.state is ConfigEntryState.LOADED
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.unreadable_devices[SENSOR_ID]["probe"] == {"error": "boom"}


async def test_diagnostics_list_the_skipped_device_redacted(hass: HomeAssistant) -> None:
    entry, _ = await _setup_account(hass, [SENSOR_ID, DEVICE_ID])
    diag = await async_get_config_entry_diagnostics(hass, entry)
    noted = diag["unreadable_devices"][SENSOR_ID]
    assert noted["type"] == "SaunaSensor"
    assert noted["probe"]["state"]["state"]["temp"] == 21
    assert "HomeNet" not in json.dumps(diag)


async def test_diagnostics_redact_attribute_lists(hass: HomeAssistant) -> None:
    """The device list names its attributes in values, not in keys."""
    entry, _ = await _setup_account(hass, [SENSOR_ID, DEVICE_ID])
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.unreadable_devices[SENSOR_ID]["list_entry"] = {
        "deviceId": SENSOR_ID, "type": "SaunaSensor",
        "attr": [
            {"key": "serialNumber", "value": "SN123456"},
            {"key": "name", "value": "Garden sauna"},
        ],
    }
    diag = await async_get_config_entry_diagnostics(hass, entry)
    attrs = diag["unreadable_devices"][SENSOR_ID]["list_entry"]["attr"]
    assert "SN123456" not in json.dumps(diag)
    assert attrs[0]["key"] == "serialNumber"
    assert attrs[1] == {"key": "name", "value": "Garden sauna"}


async def test_xenio_setup_still_retries_on_a_failed_first_poll(
    hass: HomeAssistant,
) -> None:
    """Skipping applies to the cloud's 404 only, not to any connection error."""
    api = FakeApi(API_PROVIDER_MYHARVIA)
    api.async_get_device_state = AsyncMock(side_effect=HarviaConnectionError("HTTP 500"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_API_PROVIDER: API_PROVIDER_MYHARVIA,
            CONF_HEATER_MODEL: "other",
            CONF_HEATER_POWER: "10.8",
        },
        unique_id="user@example.com",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.harvia_sauna.create_api_client", return_value=api):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


def _registered(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert (DOMAIN, DEVICE_ID) in device.identifiers
    return device


def _client(hass: HomeAssistant) -> HarviaIoApiClient:
    client = HarviaIoApiClient(hass, "user@example.com", "secret")
    client._async_get_valid_id_token = AsyncMock(return_value="token")
    return client


@pytest.mark.parametrize(
    ("status", "not_found"), [(404, True), (400, False), (500, False)]
)
async def test_only_http_404_is_a_not_found_error(
    hass: HomeAssistant, aioclient_mock, status: int, not_found: bool
) -> None:
    url = "https://example.invalid/devices/state"
    aioclient_mock.get(url, status=status, text='{"error":"x"}')
    with pytest.raises(HarviaConnectionError) as raised:
        await _client(hass)._async_raw_request_once("GET", url)
    assert isinstance(raised.value, HarviaNotFoundError) is not_found
    assert f"HTTP {status}" in str(raised.value)


async def test_the_push_receiver_is_the_first_readable_device(
    hass: HomeAssistant,
) -> None:
    client = _client(hass)
    client.async_get_devices = AsyncMock(
        return_value=[{"device_id": SENSOR_ID}, {"device_id": DEVICE_ID}]
    )
    assert await client.async_get_receiver_id() == SENSOR_ID  # as before
    client.ignore_device(SENSOR_ID)
    assert await client.async_get_receiver_id() == DEVICE_ID
    client.ignore_device(DEVICE_ID)
    assert await client.async_get_receiver_id() is None


async def test_the_probe_keeps_answers_and_errors(hass: HomeAssistant) -> None:
    client = _client(hass)
    asked: list[tuple] = []

    async def fake_rest(service, method, path, params=None, **kw):
        asked.append((service, method, path, params))
        if service == "data":
            raise HarviaConnectionError("HTTP 400: cabinId required")
        return {"shadowName": "classic", "state": {"temp": 21}}

    client._async_rest_request = fake_rest
    assert await client.async_probe_device(SENSOR_ID) == {
        "state": {"shadowName": "classic", "state": {"temp": 21}},
        "latest_data": {"error": "HTTP 400: cabinId required"},
    }
    # Read only, and without a cabin: that is the point of the probe
    assert asked == [
        ("device", "GET", "/devices/state", {"deviceId": SENSOR_ID}),
        ("data", "GET", "/data/latest-data", {"deviceId": SENSOR_ID}),
    ]

    client._async_rest_request = AsyncMock(side_effect=HarviaAuthError("HTTP 403"))
    assert await client.async_probe_device(SENSOR_ID) == {
        "state": {"error": "HTTP 403"},
        "latest_data": {"error": "HTTP 403"},
    }


# --- #16: firmware --------------------------------------------------------


def test_fenix_firmware_name_is_mapped() -> None:
    assert _normalize_state_payload("d", {"deviceFwVersion": "1.2.12"})["fwVersion"] == "1.2.12"
    both = _normalize_state_payload("d", {"fwVersion": "2.0", "deviceFwVersion": "1.2.12"})
    assert both["fwVersion"] == "2.0"
    assert "fwVersion" not in _normalize_state_payload("d", {"targetTemp": 80})


async def test_firmware_from_a_later_push_reaches_the_device_registry(
    hass: HomeAssistant,
) -> None:
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert _registered(hass, entry).sw_version is None

    manager = HarviaIoWebSocketManager(api, coordinator._async_handle_ws_update)
    # The device-info message of #16: reported is a JSON string
    await manager._handle_message("device", {"payload": {"data": {
        "devicesStatesUpdateFeed": {"item": {
            "deviceId": DEVICE_ID,
            "reported": json.dumps({"deviceFwVersion": "1.2.12", "signalStrength": -57}),
        }}}}})
    await hass.async_block_till_done()

    assert _registered(hass, entry).sw_version == "1.2.12"
    assert coordinator.data.devices[DEVICE_ID].wifi_rssi == -57


async def test_firmware_known_at_setup_is_unchanged(hass: HomeAssistant) -> None:
    """Xenio: the polled state carries it, the entities register it as before."""
    api_state = {"fwVersion": "2.3.4"}
    with patch.dict("tests.test_setup.XENIO_STATE", api_state):
        entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    assert _registered(hass, entry).sw_version == "2.3.4"
    coordinator = hass.data[DOMAIN][entry.entry_id]
    with patch.object(dr.DeviceRegistry, "async_update_device") as update:
        await _poll(hass, coordinator)
        await _poll(hass, coordinator)
    update.assert_not_called()


# --- #16: connection state ------------------------------------------------


def test_connection_state_is_read_from_the_polled_payload() -> None:
    polled = {"state": {"targetTemp": 80}, "connectionState": {
        "connected": False, "updatedTimestamp": 1788780621188}}
    assert _normalize_state_payload("d", polled)["cloudConnected"] is False
    polled["connectionState"]["connected"] = True
    assert _normalize_state_payload("d", polled)["cloudConnected"] is True
    for unusable in ({}, {"connectionState": None}, {"connectionState": {}},
                     {"connectionState": {"connected": "false"}},
                     {"connectionState": {"connected": 0}}):
        assert "cloudConnected" not in _normalize_state_payload("d", {"state": {}, **unusable})


async def test_a_disconnected_fenix_is_unavailable_until_it_is_back(
    hass: HomeAssistant,
) -> None:
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

    api.state_overrides["cloudConnected"] = False
    await _poll(hass, coordinator)
    assert coordinator.last_update_success  # the poll itself worked
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE

    api.state_overrides["cloudConnected"] = True
    await _poll(hass, coordinator)
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


async def test_the_state_feed_reports_a_disconnect_without_a_state(
    hass: HomeAssistant,
) -> None:
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    manager = HarviaIoWebSocketManager(api, coordinator._async_handle_ws_update)

    def message(connected) -> dict:
        item = {"deviceId": DEVICE_ID, "reported": None}
        if connected is not None:
            # The feed sends the timestamp as a string (#16)
            item["connectionState"] = {
                "connected": connected, "updatedTimestamp": "1788780621188"}
        return {"payload": {"data": {"devicesStatesUpdateFeed": {"item": item}}}}

    device = coordinator.data.devices[DEVICE_ID]
    before = (device._state_seq, device._last_update)
    with patch.object(coordinator, "async_update_listeners") as notified:
        await manager._handle_message("device", message(None))
    notified.assert_not_called()  # still ignored

    await manager._handle_message("device", message(False))
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE
    # Neither a state push nor a sign of life
    assert (device._state_seq, device._last_update) == before

    await manager._handle_message("device", message(True))
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


async def test_a_frame_just_pushed_outweighs_a_reported_disconnect(
    hass: HomeAssistant,
) -> None:
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    manager = HarviaIoWebSocketManager(api, coordinator._async_handle_ws_update)
    device = coordinator.data.devices[DEVICE_ID]

    api.state_overrides["cloudConnected"] = False
    await _poll(hass, coordinator)
    assert coordinator.is_device_offline(DEVICE_ID)

    await manager._handle_message("data", {"payload": {"data": {
        "devicesMeasurementsUpdateFeed": {"item": {
            "deviceId": DEVICE_ID, "timestamp": "1789104671089", "type": "Fenix",
            "data": json.dumps({"temp": 40}),
        }}}}})
    await hass.async_block_till_done()
    assert device.cloud_connected is False
    assert not coordinator.is_device_offline(DEVICE_ID)
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

    device._device_frame_ts -= DEVICE_STALE_TIMEOUT + 1
    assert coordinator.is_device_offline(DEVICE_ID)


async def test_a_newer_polled_frame_also_outweighs_a_reported_disconnect(
    hass: HomeAssistant,
) -> None:
    """Without pushes the poll is the only way to see that the device sends."""
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    api.state_overrides["cloudConnected"] = False

    # The first frame seen proves nothing: it may be days old.
    api.telemetry_overrides["timestamp"] = "1789104671089"
    await _poll(hass, coordinator)
    assert coordinator.is_device_offline(DEVICE_ID)
    await _poll(hass, coordinator)  # the same frame again
    assert coordinator.is_device_offline(DEVICE_ID)

    api.telemetry_overrides["timestamp"] = "1789104701089"
    await _poll(hass, coordinator)
    assert not coordinator.is_device_offline(DEVICE_ID)
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


async def test_the_polled_payload_of_a_fenix_drives_availability(
    hass: HomeAssistant,
) -> None:
    """From the cloud's own payload shape (#9, #16) through the real client."""
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    payload = {
        "deviceId": DEVICE_ID, "shadowName": "C1",
        "state": {"targetTemp": 95, "heater": {"on": 0}, "saunaStatus": 0},
        "version": 4346, "timestamp": 1789076406,
        "connectionState": {"connected": False, "updatedTimestamp": 1788780621188},
    }
    client = _client(hass)
    client._async_rest_request = AsyncMock(return_value=payload)
    api.async_get_device_state = client.async_get_device_state

    await _poll(hass, coordinator)
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE
    payload["connectionState"]["connected"] = True
    await _poll(hass, coordinator)
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


async def test_a_feed_item_with_state_and_connection_applies_both(
    hass: HomeAssistant,
) -> None:
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    manager = HarviaIoWebSocketManager(api, coordinator._async_handle_ws_update)
    await manager._handle_message("device", {"payload": {"data": {
        "devicesStatesUpdateFeed": {"item": {
            "deviceId": DEVICE_ID,
            "reported": json.dumps({"heater": {"on": 1}, "saunaStatus": 1}),
            "connectionState": {"connected": False, "updatedTimestamp": "1"},
        }}}}})
    await hass.async_block_till_done()
    device = coordinator.data.devices[DEVICE_ID]
    assert device.active
    assert device.cloud_connected is False
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE


async def test_a_connection_message_does_not_discard_a_running_poll(
    hass: HomeAssistant,
) -> None:
    """It carries no state, so the state the poll just read must still win."""
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    manager = HarviaIoWebSocketManager(api, coordinator._async_handle_ws_update)
    real = api.async_get_latest_device_data

    async def connection_message_during_the_poll(device_id):
        await manager._handle_message("device", {"payload": {"data": {
            "devicesStatesUpdateFeed": {"item": {
                "deviceId": DEVICE_ID, "reported": None,
                "connectionState": {"connected": True, "updatedTimestamp": "1"},
            }}}}})
        return await real(device_id)

    api.state_overrides["active"] = 1
    api.async_get_latest_device_data = connection_message_during_the_poll
    await _poll(hass, coordinator)
    assert coordinator.data.devices[DEVICE_ID].active


async def test_xenio_is_never_offline_by_this_rule(hass: HomeAssistant) -> None:
    entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.data.devices[DEVICE_ID].cloud_connected is None
    assert not coordinator.is_device_offline(DEVICE_ID)
    assert not coordinator.is_device_offline("unknown-device")
