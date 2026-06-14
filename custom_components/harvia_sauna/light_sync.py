"""Bidirectional sync between the Harvia panel light button and HA lights.

Use case: the sauna light is NOT wired to the Harvia power unit (e.g. Hue
strips controlled by HA), but the light button on the Harvia panel should
still switch those HA light entities — and optionally vice versa.

Loop protection:
- HA -> Panel direction ignores light state changes that originate from our
  own service-call Context (we created them in the Panel -> HA direction).
- Panel -> HA direction consumes the expected WebSocket echo that follows
  our own HA -> Panel command instead of re-applying it to the lights.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_LIGHT_SYNC_MODE,
    CONF_LINKED_LIGHTS,
    DEFAULT_LIGHT_SYNC_MODE,
    LIGHT_SYNC_BIDIRECTIONAL,
    LIGHT_SYNC_DEBOUNCE_SEC,
    LIGHT_SYNC_OFF,
)
from .coordinator import HarviaSaunaCoordinator

_LOGGER = logging.getLogger(__name__)

# Keep a bounded set of our own context IDs for loop detection
MAX_TRACKED_CONTEXTS = 50


class HarviaLightSync:
    """Couple the Harvia panel light state with HA light entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: HarviaSaunaCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize light sync from config entry options."""
        self._hass = hass
        self._coordinator = coordinator
        self._mode: str = entry.options.get(
            CONF_LIGHT_SYNC_MODE, DEFAULT_LIGHT_SYNC_MODE
        )
        self._linked_lights: list[str] = list(
            entry.options.get(CONF_LINKED_LIGHTS, [])
        )
        self._unsubs: list[Any] = []
        # Last known panel light state per device (for edge detection —
        # we only react to actual transitions, never force-sync absolute
        # state after restarts/reconnects)
        self._last_panel_state: dict[str, bool | None] = {}
        # Expected panel state per device after our own HA -> Panel command
        self._expected_panel: dict[str, bool] = {}
        # Context IDs of service calls we issued ourselves
        self._own_context_ids: list[str] = []
        # Debounce handle for HA -> Panel commands
        self._debounce_handle = None
        self._pending_panel_target: dict[str, bool] = {}

    @property
    def enabled(self) -> bool:
        """Return True if light sync is active."""
        return self._mode != LIGHT_SYNC_OFF and bool(self._linked_lights)

    async def async_setup(self) -> None:
        """Set up listeners."""
        if not self.enabled:
            return

        # Validate linked entities exist; warn (don't fail) if missing
        for entity_id in self._linked_lights:
            if self._hass.states.get(entity_id) is None:
                _LOGGER.warning(
                    "Linked light %s not found — it will be skipped until "
                    "it becomes available",
                    entity_id,
                )

        # Seed edge detection with current panel state (no initial sync!)
        if self._coordinator.data:
            for device_id, device in self._coordinator.data.devices.items():
                self._last_panel_state[device_id] = device.lights_on

        # Panel -> HA: listen to coordinator updates
        self._unsubs.append(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

        # HA -> Panel: listen to linked light state changes
        if self._mode == LIGHT_SYNC_BIDIRECTIONAL:
            self._unsubs.append(
                async_track_state_change_event(
                    self._hass, self._linked_lights, self._handle_light_change
                )
            )

        _LOGGER.debug(
            "Light sync enabled (mode=%s, lights=%s)",
            self._mode,
            self._linked_lights,
        )

    @callback
    def async_teardown(self) -> None:
        """Remove listeners and cancel pending work."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
            self._debounce_handle = None

    # ── Panel -> HA ──────────────────────────────────────────────────

    @callback
    def _handle_coordinator_update(self) -> None:
        """Detect panel light transitions and mirror them to HA lights."""
        if not self._coordinator.data:
            return
        for device_id, device in self._coordinator.data.devices.items():
            previous = self._last_panel_state.get(device_id)
            current = device.lights_on
            self._last_panel_state[device_id] = current

            if previous is None or current == previous:
                continue  # no transition (or first observation — never sync)

            # Is this the echo of our own HA -> Panel command?
            expected = self._expected_panel.pop(device_id, None)
            if expected is not None and expected == current:
                _LOGGER.debug(
                    "Consumed expected panel echo (device %s, state %s)",
                    device_id,
                    current,
                )
                continue

            _LOGGER.debug(
                "Panel light transition %s -> %s (device %s) — syncing "
                "%d HA light(s)",
                previous,
                current,
                device_id,
                len(self._linked_lights),
            )
            self._hass.async_create_background_task(
                self._async_set_ha_lights(current), "harvia_lightsync_set"
            )

    async def _async_set_ha_lights(self, turn_on: bool) -> None:
        """Switch all linked HA lights, remembering our own context."""
        context = Context()
        self._own_context_ids.append(context.id)
        if len(self._own_context_ids) > MAX_TRACKED_CONTEXTS:
            self._own_context_ids = self._own_context_ids[-MAX_TRACKED_CONTEXTS:]

        targets = []
        for entity_id in self._linked_lights:
            state = self._hass.states.get(entity_id)
            if state is None or state.state == "unavailable":
                _LOGGER.warning(
                    "Skipping unavailable linked light %s", entity_id
                )
                continue
            targets.append(entity_id)
        if not targets:
            return

        await self._hass.services.async_call(
            "light",
            "turn_on" if turn_on else "turn_off",
            {"entity_id": targets},
            blocking=False,
            context=context,
        )

    # ── HA -> Panel (bidirectional only) ─────────────────────────────

    @callback
    def _handle_light_change(self, event: Event) -> None:
        """Mirror HA light changes back to the panel (any-on semantics)."""
        # Ignore changes we caused ourselves (Panel -> HA direction)
        if event.context and event.context.id in self._own_context_ids:
            return

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        # Aggregate: panel is "on" if ANY linked light is on
        any_on = False
        for entity_id in self._linked_lights:
            state = self._hass.states.get(entity_id)
            if state is not None and state.state == "on":
                any_on = True
                break

        if not self._coordinator.data:
            return

        for device_id, device in self._coordinator.data.devices.items():
            if device.lights_on == any_on:
                continue
            self._pending_panel_target[device_id] = any_on

        if self._pending_panel_target:
            self._schedule_panel_update()

    @callback
    def _schedule_panel_update(self) -> None:
        """Debounce HA -> Panel commands to avoid flooding the cloud API."""
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
        self._debounce_handle = self._hass.loop.call_later(
            LIGHT_SYNC_DEBOUNCE_SEC,
            lambda: self._hass.async_create_background_task(
                self._async_flush_panel_update(), "harvia_lightsync_flush"
            ),
        )

    async def _async_flush_panel_update(self) -> None:
        """Send pending panel light commands."""
        self._debounce_handle = None
        pending = dict(self._pending_panel_target)
        self._pending_panel_target.clear()

        for device_id, target in pending.items():
            device = (
                self._coordinator.data.devices.get(device_id)
                if self._coordinator.data
                else None
            )
            if device is None or device.lights_on == target:
                continue
            _LOGGER.debug(
                "HA light change — updating panel light to %s (device %s)",
                target,
                device_id,
            )
            self._expected_panel[device_id] = target
            try:
                await self._coordinator.async_request_state_change(
                    device_id, {"light": 1 if target else 0}
                )
            except Exception as err:  # noqa: BLE001 — never break light handling
                self._expected_panel.pop(device_id, None)
                _LOGGER.warning(
                    "Failed to update panel light (device %s): %s",
                    device_id,
                    err,
                )
                continue
            # Optimistic local update so edge detection doesn't re-trigger
            device.lights_on = target
            self._last_panel_state[device_id] = target
