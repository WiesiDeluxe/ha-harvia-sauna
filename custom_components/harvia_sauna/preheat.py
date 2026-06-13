"""Smart preheat: switch the heater on in time to be ready at a target.

Given a "ready at" datetime and a target temperature, the scheduler asks
the heating model how long the heat-up takes from the current reference
temperature, subtracts a safety buffer, and switches the heater on at the
computed start time. The estimate is recomputed periodically because the
cabin temperature drifts before the start.

Robustness:
- recompute every PREHEAT_RECOMPUTE_INTERVAL_MIN minutes
- if the start time is already in the past (or within the next interval),
  start immediately
- if the sauna is already heating, absorb the schedule (consider it
  fulfilled)
- after starting, verify after PREHEAT_START_VERIFY_DELAY_SEC and retry up
  to PREHEAT_START_MAX_RETRIES times (Xenio requires panel re-enable after
  a door opening — remote start can silently fail, so this MUST be loud)
- on definitive failure: fire EVENT_PREHEAT_FAILED and create a
  persistent_notification
- persisted across restarts and re-armed on setup
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PREHEAT_BUFFER_MIN,
    DEFAULT_PREHEAT_BUFFER_MIN,
    DOMAIN,
    EVENT_PREHEAT_FAILED,
    EVENT_PREHEAT_STARTED,
    PREHEAT_RECOMPUTE_INTERVAL_MIN,
    PREHEAT_START_MAX_RETRIES,
    PREHEAT_START_VERIFY_DELAY_SEC,
    STORAGE_KEY_PREHEAT,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class PreheatScheduler:
    """Schedule a heater start to reach a target temperature by a time."""

    def __init__(self, hass: HomeAssistant, coordinator, entry) -> None:
        """Initialize the scheduler."""
        self._hass = hass
        self._coordinator = coordinator
        self._entry = entry
        self._store: Store = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREHEAT}_{entry.entry_id}"
        )
        # Active schedule: {"ready_at": datetime, "target_temp": float,
        #                   "device_id": str}
        self._schedule: dict[str, Any] | None = None
        self._planned_start: dt.datetime | None = None
        self._unsub_interval: Any = None
        self._unsub_start: Any = None
        self._retries = 0
        self._started = False

    @property
    def buffer_min(self) -> int:
        """Return the configured safety buffer in minutes."""
        return int(
            self._entry.options.get(
                CONF_PREHEAT_BUFFER_MIN, DEFAULT_PREHEAT_BUFFER_MIN
            )
        )

    @property
    def ready_at(self) -> dt.datetime | None:
        """Return the scheduled ready-at time, or None."""
        return self._schedule["ready_at"] if self._schedule else None

    @property
    def planned_start(self) -> dt.datetime | None:
        """Return the computed heater start time, or None."""
        return self._planned_start

    # ── Persistence ──────────────────────────────────────────────────

    async def async_load(self) -> None:
        """Re-arm a persisted schedule after a restart."""
        data = await self._store.async_load()
        if not data or not data.get("ready_at"):
            return
        try:
            ready_at = dt_util.parse_datetime(data["ready_at"])
        except (ValueError, TypeError):
            return
        if ready_at is None:
            return
        if ready_at <= dt_util.utcnow():
            _LOGGER.debug("Preheat: persisted schedule already past — dropping")
            await self._store.async_remove()
            return
        self._schedule = {
            "ready_at": ready_at,
            "target_temp": float(data.get("target_temp"))
            if data.get("target_temp") is not None
            else None,
            "device_id": data.get("device_id"),
        }
        _LOGGER.info("Preheat: re-armed schedule for %s", ready_at.isoformat())
        self._arm_interval()
        self._recompute()

    async def _async_persist(self) -> None:
        if self._schedule is None:
            await self._store.async_remove()
            return
        await self._store.async_save(
            {
                "ready_at": self._schedule["ready_at"].isoformat(),
                "target_temp": self._schedule["target_temp"],
                "device_id": self._schedule["device_id"],
            }
        )

    # ── Public API ───────────────────────────────────────────────────

    async def async_set_schedule(
        self,
        ready_at: dt.datetime,
        target_temp: float | None,
        device_id: str | None = None,
    ) -> None:
        """Set (or replace) the preheat schedule."""
        if device_id is None:
            device_id = self._default_device_id()
        if ready_at.tzinfo is None:
            ready_at = dt_util.as_utc(ready_at)
        target = target_temp
        if target is None:
            device = self._device(device_id)
            target = float(device.target_temp) if device and device.target_temp else None
        self._schedule = {
            "ready_at": ready_at,
            "target_temp": target,
            "device_id": device_id,
        }
        self._started = False
        self._retries = 0
        await self._async_persist()
        self._arm_interval()
        self._recompute()
        _LOGGER.info(
            "Preheat: scheduled ready-at %s (target %s°C, device %s)",
            ready_at.isoformat(),
            target,
            device_id,
        )

    async def async_cancel(self) -> None:
        """Cancel the active schedule."""
        self._clear_timers()
        self._schedule = None
        self._planned_start = None
        self._started = False
        await self._store.async_remove()
        _LOGGER.info("Preheat: schedule cancelled")

    def async_teardown(self) -> None:
        """Remove timers (called on unload)."""
        self._clear_timers()

    # ── Internals ────────────────────────────────────────────────────

    def _default_device_id(self) -> str | None:
        data = self._coordinator.data
        if data and data.devices:
            return next(iter(data.devices))
        return None

    def _device(self, device_id: str | None):
        data = self._coordinator.data
        if not data or device_id is None:
            return None
        return data.devices.get(device_id)

    def _clear_timers(self) -> None:
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None
        if self._unsub_start is not None:
            self._unsub_start()
            self._unsub_start = None

    def _arm_interval(self) -> None:
        if self._unsub_interval is not None:
            return
        self._unsub_interval = async_track_time_interval(
            self._hass,
            self._handle_interval,
            dt.timedelta(minutes=PREHEAT_RECOMPUTE_INTERVAL_MIN),
        )

    async def _handle_interval(self, _now: dt.datetime) -> None:
        self._recompute()

    def _recompute(self) -> None:
        """Recompute the start time and (re)arm the start trigger."""
        if self._schedule is None or self._started:
            return
        device_id = self._schedule["device_id"]
        device = self._device(device_id)
        if device is None:
            return

        # Already heating? Absorb the schedule.
        if device.active:
            _LOGGER.info("Preheat: sauna already heating — schedule fulfilled")
            self._hass.async_create_task(self.async_cancel())
            return

        model = getattr(self._coordinator, "heating_model", None)
        target = self._schedule["target_temp"]
        ref = self._coordinator._get_external_temp_c(device)
        if ref is None:
            ref = device.current_temp
        if model is None or target is None or ref is None:
            return

        minutes = model.estimate_minutes(float(ref), float(target))
        if minutes is None:
            return
        total = minutes + self.buffer_min
        start_at = self._schedule["ready_at"] - dt.timedelta(minutes=total)
        self._planned_start = start_at

        now = dt_util.utcnow()
        # Start now if we're at/after the computed start, or it falls within
        # the next recompute interval (so we don't miss it between ticks).
        threshold = now + dt.timedelta(minutes=PREHEAT_RECOMPUTE_INTERVAL_MIN)
        if self._unsub_start is not None:
            self._unsub_start()
            self._unsub_start = None
        if start_at <= threshold:
            when = max(start_at, now)
            self._unsub_start = async_track_point_in_time(
                self._hass, self._handle_start, when
            )
            _LOGGER.debug("Preheat: start armed for %s", when.isoformat())

    async def _handle_start(self, _now: dt.datetime) -> None:
        """Fire the heater start and begin verification."""
        self._unsub_start = None
        if self._schedule is None or self._started:
            return
        self._started = True
        await self._async_attempt_start()

    async def _async_attempt_start(self) -> None:
        schedule = self._schedule
        if schedule is None:
            return
        device_id = schedule["device_id"]
        payload: dict[str, Any] = {"active": 1}
        if schedule["target_temp"] is not None:
            payload["targetTemp"] = int(schedule["target_temp"])
        _LOGGER.info(
            "Preheat: starting heater (device %s, attempt %d)",
            device_id,
            self._retries + 1,
        )
        try:
            await self._coordinator.async_request_state_change(
                device_id, payload
            )
        except Exception as err:  # noqa: BLE001 - surfaced via retry/event
            _LOGGER.warning("Preheat: start command failed: %s", err)
        self._unsub_start = async_call_later(
            self._hass, PREHEAT_START_VERIFY_DELAY_SEC, self._handle_verify
        )

    async def _handle_verify(self, _now: dt.datetime) -> None:
        self._unsub_start = None
        if self._schedule is None:
            return
        device = self._device(self._schedule["device_id"])
        if device is not None and device.active:
            _LOGGER.info("Preheat: heater confirmed running")
            self._hass.bus.async_fire(
                EVENT_PREHEAT_STARTED,
                {
                    "device_id": self._schedule["device_id"],
                    "target_temp": self._schedule["target_temp"],
                    "ready_at": self._schedule["ready_at"].isoformat(),
                },
            )
            await self.async_cancel()
            return

        self._retries += 1
        if self._retries < PREHEAT_START_MAX_RETRIES:
            _LOGGER.warning(
                "Preheat: heater not running yet — retry %d/%d",
                self._retries,
                PREHEAT_START_MAX_RETRIES,
            )
            await self._async_attempt_start()
            return

        # Definitive failure
        device_id = self._schedule["device_id"]
        _LOGGER.error(
            "Preheat: heater failed to start after %d attempts (device %s)",
            PREHEAT_START_MAX_RETRIES,
            device_id,
        )
        self._hass.bus.async_fire(
            EVENT_PREHEAT_FAILED,
            {
                "device_id": device_id,
                "target_temp": self._schedule["target_temp"],
                "ready_at": self._schedule["ready_at"].isoformat(),
                "reason": "heater_not_running_after_retries",
            },
        )
        self._hass.async_create_task(
            self._hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Sauna-Vorheizen fehlgeschlagen",
                    "message": (
                        "Die Sauna konnte nicht automatisch gestartet werden. "
                        "Möglicherweise ist die Fernsteuerung am Panel nicht "
                        "freigegeben (z. B. nach einer Türöffnung). Bitte am "
                        "Panel prüfen."
                    ),
                    "notification_id": f"{DOMAIN}_preheat_failed_{device_id}",
                },
                blocking=False,
            )
        )
        await self.async_cancel()
