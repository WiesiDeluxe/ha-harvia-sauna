"""Auth-resilience tests (issue #8).

Transient cloud/network problems at token-refresh time must never trigger
Home Assistant's reauth flow; genuinely wrong credentials still must.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import botocore.exceptions
import pytest
from homeassistant.core import HomeAssistant

from custom_components.harvia_sauna.api import HarviaApiClient
from custom_components.harvia_sauna.const import (
    API_PROVIDER_MYHARVIA,
    AUTH_FAILURES_BEFORE_REAUTH,
    DOMAIN,
)
from custom_components.harvia_sauna.errors import HarviaAuthError, HarviaConnectionError

from .test_setup import _setup


def _client_error(code: str) -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": code}}, "InitiateAuth"
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("NotAuthorizedException", HarviaAuthError),      # wrong password
        ("UserNotFoundException", HarviaAuthError),
        ("TooManyRequestsException", HarviaConnectionError),  # throttled
        ("InternalErrorException", HarviaConnectionError),
        ("ForbiddenException", HarviaConnectionError),    # WAF
    ],
)
async def test_cognito_error_codes_are_classified(
    hass: HomeAssistant, code: str, expected: type[Exception]
) -> None:
    """Only real credential errors become HarviaAuthError."""
    client = HarviaApiClient(hass, "user@example.com", "secret")
    cognito = MagicMock()
    cognito.authenticate.side_effect = _client_error(code)
    with patch.object(client, "_async_get_cognito_client", AsyncMock(return_value=cognito)):
        with pytest.raises(expected):
            await client.async_authenticate()


async def test_graphql_401_is_retried_exactly_once(hass: HomeAssistant) -> None:
    """A rejected token triggers one re-auth + retry; a second rejection fails."""
    client = HarviaApiClient(hass, "user@example.com", "secret")
    once = AsyncMock(side_effect=[HarviaAuthError("HTTP 401"), {"data": {"ok": 1}}])
    with patch.object(client, "_async_graphql_once", once):
        assert await client.async_graphql_request("device", {"query": "x"}) == {"data": {"ok": 1}}
    assert once.await_count == 2

    twice = AsyncMock(side_effect=[HarviaAuthError("HTTP 401"), HarviaAuthError("HTTP 401")])
    with patch.object(client, "_async_graphql_once", twice):
        with pytest.raises(HarviaAuthError):
            await client.async_graphql_request("device", {"query": "x"})
    assert twice.await_count == 2  # retried once, not looped


def _reauth_flows(hass: HomeAssistant) -> list:
    return [
        f for f in hass.config_entries.flow.async_progress()
        if f["handler"] == DOMAIN and f["context"].get("source") == "reauth"
    ]


async def test_reauth_only_after_consecutive_auth_failures(hass: HomeAssistant) -> None:
    """Transient auth failures retry; persistent ones escalate to reauth."""
    entry, api = await _setup(hass, API_PROVIDER_MYHARVIA)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    api.async_get_device_state = AsyncMock(side_effect=HarviaAuthError("HTTP 401"))

    for _ in range(AUTH_FAILURES_BEFORE_REAUTH - 1):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert not _reauth_flows(hass), "reauth started too early"

    # One success in between resets the counter
    api.async_get_device_state = AsyncMock(return_value={"active": 0, "statusCodes": 4108})
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator._auth_failures == 0

    # Persistent failure (e.g. password really changed) still reaches reauth
    api.async_get_device_state = AsyncMock(side_effect=HarviaAuthError("NotAuthorizedException"))
    for _ in range(AUTH_FAILURES_BEFORE_REAUTH):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert _reauth_flows(hass), "wrong credentials must still trigger reauth"
