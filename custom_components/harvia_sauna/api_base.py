"""Provider-neutral API client interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable


class HarviaApiClientBase(ABC):
    """Abstract interface for Harvia API providers."""

    supports_push_updates: bool = False

    @abstractmethod
    async def async_authenticate(self) -> bool:
        """Authenticate API client."""

    @abstractmethod
    async def async_get_user_data(self) -> dict:
        """Return current user metadata."""

    @abstractmethod
    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return devices as normalized list with at least `device_id`."""

    @abstractmethod
    async def async_get_device_state(self, device_id: str) -> dict:
        """Return normalized device state."""

    @abstractmethod
    async def async_get_latest_device_data(self, device_id: str) -> dict:
        """Return normalized latest telemetry."""

    @abstractmethod
    async def async_request_state_change(
        self, device_id: str, payload: dict
    ) -> dict:
        """Send a device state change request."""

    async def async_start_push_updates(
        self, on_device_update: Callable[[dict], Awaitable[None]]
    ) -> None:
        """Start realtime updates if provider supports it."""

    async def async_stop_push_updates(self) -> None:
        """Stop realtime updates if provider supports it."""

    @property
    def push_connected(self) -> bool:
        """Return True if provider push channel is connected."""
        return False

    @property
    def push_connections_info(self) -> list[dict[str, Any]]:
        """Return push connection info for diagnostics."""
        return []
