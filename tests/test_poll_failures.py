"""A single failed poll must not mark every entity unavailable.

Measured on the maintainer's Xenio: all entities went unavailable for exactly
one poll interval on 1 Oct (03:42), 7 Oct (03:59) and 8 Oct 2026 (14:02), the
last one logged as "Cannot connect to host ...appsync-api... [Timeout while
contacting DNS servers]". The next poll five minutes later brought them back.
"""
import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.harvia_sauna.const import (
    API_PROVIDER_MYHARVIA,
    CONF_API_PROVIDER,
    CONF_HEATER_MODEL,
    CONF_HEATER_POWER,
    DOMAIN,
    POLL_FAILURES_BEFORE_UNAVAILABLE,
)
from custom_components.harvia_sauna.coordinator import DEVICE_STALE_TIMEOUT
from custom_components.harvia_sauna.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.harvia_sauna.errors import HarviaAuthError, HarviaConnectionError

from .test_setup import DEVICE_ID, FakeApi, _setup

CLIMATE = "climate.sauna_thermostat"
DNS_TIMEOUT = OSError(
    "Cannot connect to host aw3cv5hppjhbxpy2ceg6iz5obq.appsync-api.eu-west-1."
    "amazonaws.com:443 ssl:default [Timeout while contacting DNS servers]"
)


async def _poll(hass: HomeAssistant, coordinator) -> None:
    await coordinator.async_refresh()
    await hass.async_block_till_done()


@pytest.mark.parametrize("error", [
    DNS_TIMEOUT,                              # what the log showed on 8 Oct
    HarviaConnectionError("HTTP 503"),
    HarviaAuthError("token refresh failed"),  # transient, see issue #8
])
async def test_one_failed_poll_keeps_the_entities(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    entry, api = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    working = api.async_get_device_state
    before = hass.states.get(CLIMATE).state

    api.async_get_device_state = AsyncMock(side_effect=error)
    await _poll(hass, coordinator)
    assert coordinator.last_update_success
    assert hass.states.get(CLIMATE).state == before != STATE_UNAVAILABLE
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["coordinator"]["poll_failures"] == 1

    api.async_get_device_state = working
    with caplog.at_level(logging.INFO):
        await _poll(hass, coordinator)
    assert coordinator._poll_failures == 0
    assert "Polling recovered after 1 failed attempt(s)" in caplog.text


async def test_failures_in_a_row_still_mark_unavailable(hass: HomeAssistant) -> None:
    entry, api = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    working = api.async_get_device_state

    api.async_get_device_state = AsyncMock(side_effect=DNS_TIMEOUT)
    for _ in range(POLL_FAILURES_BEFORE_UNAVAILABLE - 1):
        await _poll(hass, coordinator)
        assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE
    await _poll(hass, coordinator)
    assert not coordinator.last_update_success
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE

    api.async_get_device_state = working
    await _poll(hass, coordinator)
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


async def test_kept_data_does_not_outlive_the_stale_guard(hass: HomeAssistant) -> None:
    """Without a push, the last data counts as stale after the usual timeout."""
    entry, api = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = coordinator.data.devices[DEVICE_ID]
    device._last_update -= DEVICE_STALE_TIMEOUT + 1  # nothing heard for 10 min

    api.async_get_device_state = AsyncMock(side_effect=DNS_TIMEOUT)
    await _poll(hass, coordinator)
    assert coordinator.last_update_success, "the failure itself was ridden out"
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE


async def test_a_failed_first_refresh_still_retries_setup(hass: HomeAssistant) -> None:
    """There is no previous data to keep: setup must be retried as before."""
    api = FakeApi(API_PROVIDER_MYHARVIA)
    api.async_get_device_state = AsyncMock(side_effect=DNS_TIMEOUT)
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
