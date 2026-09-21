"""Diagnostics redaction tests (issue #9).

Diagnostics exports are routinely pasted into public GitHub issues, so no
field may carry the account email in cleartext.
"""
import pytest
from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant

from custom_components.harvia_sauna.const import (
    API_PROVIDER_HARVIAIO,
    API_PROVIDER_MYHARVIA,
)
from custom_components.harvia_sauna.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_setup import _setup

EMAIL = "user@example.com"


@pytest.mark.parametrize("provider", [API_PROVIDER_MYHARVIA, API_PROVIDER_HARVIAIO])
async def test_diagnostics_never_leak_the_account_email(
    hass: HomeAssistant, provider: str
) -> None:
    """No part of the export may contain the account email."""
    entry, _ = await _setup(hass, provider)
    assert entry.unique_id and EMAIL in entry.unique_id  # precondition
    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["config_entry"]["unique_id"] == REDACTED
    assert diag["config_entry"]["data"]["username"] == REDACTED
    assert diag["config_entry"]["data"]["password"] == REDACTED
    assert EMAIL not in str(diag), "account email leaked in diagnostics"


async def test_raw_payloads_refresh_on_push_not_only_on_poll(
    hass: HomeAssistant,
) -> None:
    """Pushed payloads must reach the diagnostics export (issue #9).

    Every push calls async_set_updated_data(), which resets the poll timer, so
    a device pushing more often than the scan interval is never polled. When
    raw payloads were recorded in the poll path only, exports froze at the
    last poll while the entities kept updating — silently misleading anyone
    debugging from an export.
    """
    import json as _json

    entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data["harvia_sauna"][entry.entry_id]
    device_id = next(iter(coordinator.data.devices))

    before = await async_get_config_entry_diagnostics(hass, entry)
    polled = before["raw_payloads"]["last_state"].get(device_id)
    assert polled and polled["source"] == "poll", "poll must record raw state"

    await coordinator._async_handle_ws_update(
        {
            "onDataUpdates": {
                "item": {
                    "deviceId": device_id,
                    "data": _json.dumps({"temperature": 77, "heatOn": 1}),
                    "timestamp": "1789125424874",
                }
            }
        }
    )
    await hass.async_block_till_done()

    after = await async_get_config_entry_diagnostics(hass, entry)
    pushed = after["raw_payloads"]["last_telemetry"][device_id]
    assert pushed["source"] == "push"
    assert pushed["payload"]["temperature"] == 77
    assert pushed["captured_at"] >= polled["captured_at"]
    assert "generated_at" in after, "exports must carry their own capture time"


async def test_raw_payloads_redact_vendor_secrets(hass: HomeAssistant) -> None:
    """Vendor payloads are redacted by key pattern, not by an explicit list.

    The device-info websocket message carries the Wi-Fi SSID (issue #9), and
    these payload shapes are not enumerable, so anything that looks like a
    secret is redacted wherever it sits.
    """
    entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data["harvia_sauna"][entry.entry_id]
    coordinator.api.last_ws_messages = [
        {"type": "deviceInfo", "wifiSsid": "Wiesi-Home", "swVer": "2.3.4",
         "nested": {"SSID": "Guest-Net", "rssi": -67}}
    ]
    diag = await async_get_config_entry_diagnostics(hass, entry)
    msg = diag["raw_payloads"]["last_websocket_messages"][0]
    assert msg["wifiSsid"] == REDACTED
    assert msg["nested"]["SSID"] == REDACTED
    assert msg["swVer"] == "2.3.4" and msg["nested"]["rssi"] == -67
    assert "Wiesi-Home" not in str(diag) and "Guest-Net" not in str(diag)


async def test_redaction_descends_into_json_strings(hass: HomeAssistant) -> None:
    """Payloads carry AWSJSON — JSON encoded as a string (issue #9).

    Shape as measured on a Fenix: in the device-info websocket message,
    devicesStatesUpdateFeed.item.reported is a JSON *string*, so the
    `wifissid` key inside it was never visited and the b4 claim that SSIDs
    are redacted was wrong.
    """
    import json as _json

    entry, _ = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data["harvia_sauna"][entry.entry_id]
    coordinator.api.last_ws_messages = [
        {
            "endpoint": "device",
            "payload": {
                "devicesStatesUpdateFeed": {
                    "item": {
                        "deviceId": "abc",
                        "reported": _json.dumps(
                            {"wifissid": "Wiesi-Home", "rssi": -67, "name": "Süd"}
                        ),
                    }
                }
            },
        }
    ]
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert "Wiesi-Home" not in str(diag), "SSID leaked inside a JSON string"
    item = diag["raw_payloads"]["last_websocket_messages"][0]["payload"][
        "devicesStatesUpdateFeed"
    ]["item"]
    assert isinstance(item["reported"], str), "the payload shape must not change"
    inner = _json.loads(item["reported"])
    assert inner["wifissid"] == REDACTED
    assert inner["rssi"] == -67, "useful diagnostics must survive"
    assert inner["name"] == "Süd" and "Süd" in item["reported"]


async def test_redaction_handles_nested_and_malformed_json_strings(
    hass: HomeAssistant,
) -> None:
    """JSON inside JSON is followed; text that merely looks like JSON is kept."""
    import json as _json

    entry, _ = await _setup(hass, API_PROVIDER_HARVIAIO)
    coordinator = hass.data["harvia_sauna"][entry.entry_id]
    twice = _json.dumps({"reported": _json.dumps({"wifissid": "Guest-Net"})})
    coordinator.api.last_ws_messages = [
        {"payload": {"data": twice, "cmd": "{not json", "note": "[plain text"}}
    ]
    diag = await async_get_config_entry_diagnostics(hass, entry)
    payload = diag["raw_payloads"]["last_websocket_messages"][0]["payload"]
    assert "Guest-Net" not in str(diag)
    assert _json.loads(_json.loads(payload["data"])["reported"])["wifissid"] == REDACTED
    assert payload["cmd"] == "{not json" and payload["note"] == "[plain text"


async def test_raw_payloads_redact_unit_identifiers(hass: HomeAssistant) -> None:
    """MAC address and serial number identify one unit and aid no debugging."""
    entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data["harvia_sauna"][entry.entry_id]
    coordinator.api.last_ws_messages = [
        {"macAddr": "AABBCCDDEEFF", "serialNum": "2527000000", "swVer": "2.3.4"}
    ]
    diag = await async_get_config_entry_diagnostics(hass, entry)
    msg = diag["raw_payloads"]["last_websocket_messages"][0]
    assert msg["macAddr"] == REDACTED and msg["serialNum"] == REDACTED
    assert msg["swVer"] == "2.3.4"
    assert "AABBCCDDEEFF" not in str(diag) and "2527000000" not in str(diag)
