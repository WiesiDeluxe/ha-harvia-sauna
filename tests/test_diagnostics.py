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
