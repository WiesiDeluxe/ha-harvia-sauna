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
