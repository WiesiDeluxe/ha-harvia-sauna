"""Factory for API client providers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .api import HarviaApiClient
from .api_base import HarviaApiClientBase
from .api_harviaio import HarviaIoApiClient
from .const import API_PROVIDER_HARVIAIO, API_PROVIDER_MYHARVIA, CONF_API_PROVIDER


def create_api_client(
    hass: HomeAssistant, username: str, password: str, provider: str | None
) -> HarviaApiClientBase:
    """Create a provider-specific API client."""
    if provider == API_PROVIDER_HARVIAIO:
        return HarviaIoApiClient(hass, username, password)
    if provider in (None, "", API_PROVIDER_MYHARVIA):
        return HarviaApiClient(hass, username, password)
    # Unknown provider: keep backward compatibility by falling back.
    return HarviaApiClient(hass, username, password)


def get_provider_from_entry_data(entry_data: dict) -> str:
    """Return configured provider or default."""
    return entry_data.get(CONF_API_PROVIDER, API_PROVIDER_MYHARVIA)
