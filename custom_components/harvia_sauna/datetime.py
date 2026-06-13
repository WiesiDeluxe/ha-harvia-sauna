"""Datetime platform for Harvia Sauna — the preheat target time.

A single datetime entity "Next session" that reads from and writes to the
preheat scheduler. Setting a value schedules a preheat; clearing it (or
setting a past time) cancels. The heater is switched on automatically in
time to reach the target temperature by this moment.
"""

from __future__ import annotations

import datetime as dt
import logging

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import HarviaSaunaCoordinator
from .entity import HarviaBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Harvia datetime entity."""
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id in coordinator.data.devices:
        entities.append(HarviaNextSessionDateTime(coordinator, device_id))
    async_add_entities(entities)


class HarviaNextSessionDateTime(HarviaBaseEntity, DateTimeEntity):
    """The scheduled ready-at time for smart preheat."""

    _attr_translation_key = "next_session"
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self, coordinator: HarviaSaunaCoordinator, device_id: str
    ) -> None:
        """Initialize the datetime entity."""
        super().__init__(coordinator, device_id, "next_session")

    @property
    def native_value(self) -> dt.datetime | None:
        """Return the scheduled ready-at time."""
        scheduler = getattr(self.coordinator, "preheat", None)
        if scheduler is None:
            return None
        return scheduler.ready_at

    async def async_set_value(self, value: dt.datetime) -> None:
        """Schedule (or cancel) a preheat for this device."""
        scheduler = getattr(self.coordinator, "preheat", None)
        if scheduler is None:
            return
        if value <= dt_util.utcnow():
            await scheduler.async_cancel()
        else:
            await scheduler.async_set_schedule(
                value, target_temp=None, device_id=self._device_id
            )
        self.async_write_ha_state()
