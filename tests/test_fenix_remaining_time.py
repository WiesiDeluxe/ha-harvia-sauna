"""Fenix autoOffTime must reach the existing remaining-time sensor."""
import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.harvia_sauna.api_harviaio import HarviaIoApiClient
from custom_components.harvia_sauna.const import API_PROVIDER_HARVIAIO, DOMAIN
from custom_components.harvia_sauna.websocket_harviaio import HarviaIoWebSocketManager

from .test_setup import DEVICE_ID, _setup


def _measurement(data, sub_id="C1"):
    """Retain the real Fenix feed envelope; identifiers/times are synthetic."""
    return {
        "deviceId": DEVICE_ID, "subId": sub_id,
        "timestamp": "1700000000000", "sessionId": "1700000000",
        "type": "Fenix", "data": data,
    }


@pytest.mark.parametrize("transport", ["rest", "websocket"])
async def test_fenix_remaining_time_reaches_sensor(hass, transport):
    """Exercise production normalization, coordinator and entity publication.

    The frame shape comes from Fenix C1 diagnostics. Countdown values replay
    the reported local 47 -> 46 observation; this is not a hardware capture
    of the upstream implementation. onTime is deliberately different so it
    cannot accidentally supply the countdown; timeToTarget is the heat ETA.
    """
    entry, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    api.state_overrides = {"active": 1, "saunaStatus": 1}
    await coordinator.async_refresh()
    client = HarviaIoApiClient(None, "user@example.com", "unused")
    manager = HarviaIoWebSocketManager(client, coordinator._async_handle_ws_update)
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DEVICE_ID}_remaining_time"
    )
    assert entity_id is not None

    async def deliver(data, sub_id="C1"):
        item = _measurement(data, sub_id)
        if transport == "rest":
            # Replace only the network response; call the real REST adapter.
            with patch.object(client, "_async_rest_request", AsyncMock(return_value=item)):
                with patch.object(api, "async_get_latest_device_data", client.async_get_latest_device_data):
                    await coordinator.async_refresh()
        else:
            item["data"] = json.dumps(data)
            await manager._handle_message("data", {"payload": {"data": {
                "devicesMeasurementsUpdateFeed": {"receiver": DEVICE_ID, "item": item},
            }}})
        await hass.async_block_till_done()

    def remaining():
        return float(hass.states.get(entity_id).state)

    for minutes in (47, 46, 0):
        await deliver({
            "temp": 77, "hum": 0, "panelTemp": 30,
            "targetTemp": 80, "targetHum": 20,
            "autoOffTime": minutes, "onTime": 90, "timeToTarget": 3,
            "heatOn": 1, "steamOn": 0, "lightOn": 1, "fanOn": 0,
            "heaterPower": 10800, "safetyRelay": 1, "doorSafetyState": 1,
        })
        assert remaining() == minutes

    # Legacy field wins, including an explicit zero.
    await deliver({"remainingTime": 12, "autoOffTime": 46})
    assert remaining() == 12
    await deliver({"remainingTime": 0, "autoOffTime": 46})
    assert remaining() == 0

    await deliver({"autoOffTime": 46})
    await deliver({"heapSize": 2000000, "rssi": -59}, "ESP")
    assert remaining() == 46
    await deliver({"autoOffTime": None, "onTime": 0})
    assert remaining() == 46  # null is not a new countdown sample

    # A confirmed off state already makes the public sensor zero, even
    # though the coordinator retains the last valid countdown internally.
    api.state_overrides = {"active": 0, "saunaStatus": 3}
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    await deliver({"autoOffTime": None, "onTime": 0, "heatOn": 0})
    assert remaining() == 0
