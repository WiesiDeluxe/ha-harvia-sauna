"""Temperature-driven ambient lighting ("Ambilight") for the sauna.

Maps the reference temperature to a light color in up to two independent
zones (e.g. ceiling strip = full spectrum, bench strip = warm only), each
with an optional temperature offset. Active only while a session (incl.
cooldown) is running and the integration's Ambilight switch is on.

Behavioral contract (agreed design):
- Only the COLOR is driven — brightness stays untouched during the session
  so the user keeps dimming control.
- A manual color change by the user wins: Ambilight pauses for the rest of
  the session. Toggling the Ambilight switch off/on re-arms it, as does a
  new session.
- When the session ends (incl. cooldown) or the switch is turned off, the
  configured everyday standard (kelvin + brightness) is restored on all
  zone lights that are on.
- Throttle: recompute on >= 1 °C reference change or every 30 s, whichever
  comes first; 2 s transitions.
- Loop protection: our own service-call contexts are tracked; state events
  carrying them are ignored by the manual-override detector.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    AMBI_CCT_SAT_THRESHOLD,
    AMBI_CURVE_FULL,
    AMBI_CURVE_OFF,
    AMBI_CURVE_WARM,
    AMBI_MIN_INTERVAL_SEC,
    AMBI_MIN_TEMP_DELTA_C,
    AMBI_TRANSITION_SEC,
    CONF_AMBI_STANDARD_BRIGHTNESS,
    CONF_AMBI_STANDARD_KELVIN,
    CONF_AMBI_ZONE1_CURVE,
    CONF_AMBI_ZONE1_LIGHTS,
    CONF_AMBI_ZONE1_OFFSET,
    CONF_AMBI_ZONE2_CURVE,
    CONF_AMBI_ZONE2_LIGHTS,
    CONF_AMBI_ZONE2_OFFSET,
    DEFAULT_AMBI_CURVE,
    DEFAULT_AMBI_OFFSET,
    DEFAULT_AMBI_STANDARD_BRIGHTNESS,
    DEFAULT_AMBI_STANDARD_KELVIN,
    EVENT_SESSION_END,
)
from .coordinator import HarviaSaunaCoordinator

_LOGGER = logging.getLogger(__name__)

MAX_TRACKED_CONTEXTS = 50

# Grace period after our own color apply: push-based integrations (e.g. Hue)
# report state changes with their OWN context, so context tracking alone
# cannot identify our changes — within this window color events are treated
# as echoes of our own command, not as manual intervention.
SELF_APPLY_GRACE_SEC = 10.0

# Color curves: anchors of (temp °C, hue, saturation %, kelvin | None).
# Kelvin is provided for low-saturation (whitish) anchors so lights that
# support color_temp can render whites in CCT mode (visibly cleaner on Hue).
CURVE_FULL_SPECTRUM: list[tuple[float, float, float, int | None]] = [
    (30.0, 210.0, 25.0, 5500),   # cool bright white, slightly bluish
    (50.0, 35.0, 30.0, 3500),    # neutral/warm white
    (65.0, 30.0, 70.0, None),    # warm orange
    (70.0, 15.0, 90.0, None),    # red-orange
    (80.0, 0.0, 100.0, None),    # deep red
]
CURVE_WARM_ONLY: list[tuple[float, float, float, int | None]] = [
    (30.0, 28.0, 55.0, 2700),    # cosy warm white
    (60.0, 27.0, 70.0, 2200),    # very warm white
    (70.0, 15.0, 90.0, None),    # red-orange
    (80.0, 0.0, 100.0, None),    # deep red
]
CURVES = {
    AMBI_CURVE_FULL: CURVE_FULL_SPECTRUM,
    AMBI_CURVE_WARM: CURVE_WARM_ONLY,
}


def compute_zone_color(
    curve_name: str, ref_temp_c: float, offset_c: float = 0.0
) -> dict[str, Any] | None:
    """Map a reference temperature to light color service data.

    Returns {"hs_color": [h, s]} or {"color_temp_kelvin": k}, or None when
    the zone curve is off. Pure function — unit-testable without HA.
    """
    curve = CURVES.get(curve_name)
    if not curve:
        return None
    t = ref_temp_c + offset_c

    if t <= curve[0][0]:
        _, hue, sat, kelvin = curve[0]
    elif t >= curve[-1][0]:
        _, hue, sat, kelvin = curve[-1]
    else:
        hue = sat = 0.0
        kelvin = None
        for (t0, h0, s0, k0), (t1, h1, s1, k1) in zip(curve, curve[1:]):
            if t0 <= t <= t1:
                frac = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                # Hue interpolation: both curves descend monotonically in
                # hue (210 -> 0 / 28 -> 0), so linear interpolation is safe
                # (no wrap-around across 360°).
                hue = h0 + (h1 - h0) * frac
                sat = s0 + (s1 - s0) * frac
                if k0 is not None and k1 is not None:
                    kelvin = round(k0 + (k1 - k0) * frac)
                break

    if sat < AMBI_CCT_SAT_THRESHOLD and kelvin is not None:
        return {"color_temp_kelvin": int(kelvin)}
    return {"hs_color": [round(hue, 1), round(sat, 1)]}


@dataclass
class AmbilightZone:
    """One configured ambilight zone."""

    lights: list[str] = field(default_factory=list)
    curve: str = DEFAULT_AMBI_CURVE
    offset: float = DEFAULT_AMBI_OFFSET
    last_sent: dict[str, Any] | None = None  # last color payload sent

    @property
    def active(self) -> bool:
        """Return True if this zone participates."""
        return self.curve != AMBI_CURVE_OFF and bool(self.lights)


class HarviaAmbilight:
    """Drive zone light colors from the sauna reference temperature."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: HarviaSaunaCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize from config entry options."""
        self._hass = hass
        self._coordinator = coordinator
        opts = entry.options
        self._zones = [
            AmbilightZone(
                lights=list(opts.get(CONF_AMBI_ZONE1_LIGHTS, [])),
                curve=opts.get(CONF_AMBI_ZONE1_CURVE, DEFAULT_AMBI_CURVE),
                offset=float(
                    opts.get(CONF_AMBI_ZONE1_OFFSET, DEFAULT_AMBI_OFFSET)
                ),
            ),
            AmbilightZone(
                lights=list(opts.get(CONF_AMBI_ZONE2_LIGHTS, [])),
                curve=opts.get(CONF_AMBI_ZONE2_CURVE, DEFAULT_AMBI_CURVE),
                offset=float(
                    opts.get(CONF_AMBI_ZONE2_OFFSET, DEFAULT_AMBI_OFFSET)
                ),
            ),
        ]
        self._standard_kelvin = int(
            opts.get(CONF_AMBI_STANDARD_KELVIN, DEFAULT_AMBI_STANDARD_KELVIN)
        )
        self._standard_brightness_pct = int(
            opts.get(
                CONF_AMBI_STANDARD_BRIGHTNESS, DEFAULT_AMBI_STANDARD_BRIGHTNESS
            )
        )
        self._unsubs: list[Any] = []
        self._own_context_ids: list[str] = []
        self.manual_override = False
        self._last_ref_temp: float | None = None
        self._last_apply_ts: float = 0.0
        self._session_was_active = False

    @property
    def configured(self) -> bool:
        """Return True if at least one zone is usable."""
        return any(zone.active for zone in self._zones)

    @property
    def all_zone_lights(self) -> list[str]:
        """Return all light entity ids across active zones (deduped)."""
        seen: list[str] = []
        for zone in self._zones:
            if not zone.active:
                continue
            for entity_id in zone.lights:
                if entity_id not in seen:
                    seen.append(entity_id)
        return seen

    async def async_setup(self) -> None:
        """Set up listeners."""
        if not self.configured:
            return

        for entity_id in self.all_zone_lights:
            if self._hass.states.get(entity_id) is None:
                _LOGGER.warning(
                    "Ambilight zone light %s not found — it will be "
                    "skipped until it becomes available",
                    entity_id,
                )

        # Re-evaluate on every coordinator update (covers WS pushes, polling
        # and the external-sensor-driven session tracking re-runs)
        self._unsubs.append(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        # Manual override detection on the zone lights
        self._unsubs.append(
            async_track_state_change_event(
                self._hass, self.all_zone_lights, self._handle_light_change
            )
        )
        # Restore the everyday standard when the session ends
        self._unsubs.append(
            self._hass.bus.async_listen(
                EVENT_SESSION_END, self._handle_session_end
            )
        )
        _LOGGER.debug(
            "Ambilight enabled (zones=%s)",
            [(z.lights, z.curve, z.offset) for z in self._zones if z.active],
        )

    @callback
    def async_teardown(self) -> None:
        """Remove listeners."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    # ── Engine ───────────────────────────────────────────────────────

    def _ref_temp(self, device) -> float | None:
        """Reference temperature: external sensor, fallback internal."""
        ext = self._coordinator._get_external_temp_c(device)
        if ext is not None:
            return ext
        return float(device.current_temp) if device.current_temp is not None else None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-evaluate colors when sauna data changes."""
        data = self._coordinator.data
        if not data:
            return
        for device in data.devices.values():
            session_running = device._session_active or device._cooldown_active

            # New session: re-arm after a manual override in the last one
            if session_running and not self._session_was_active:
                self.manual_override = False
                self._last_ref_temp = None
            self._session_was_active = session_running

            if (
                not session_running
                or not device.ambilight_enabled
                or self.manual_override
            ):
                continue

            ref = self._ref_temp(device)
            if ref is None:
                continue  # hold current color, decide on next reading

            now = time.monotonic()
            # Throttle only small changes that are also recent. A jump of
            # at least AMBI_MIN_TEMP_DELTA_C always passes (so the color
            # never lags behind after a sensor gap), and any change passes
            # once AMBI_MIN_INTERVAL_SEC has elapsed.
            if (
                self._last_ref_temp is not None
                and abs(ref - self._last_ref_temp) < AMBI_MIN_TEMP_DELTA_C
                and now - self._last_apply_ts < AMBI_MIN_INTERVAL_SEC
            ):
                continue  # throttled (small change, recent update)
            self._last_ref_temp = ref
            self._last_apply_ts = now
            self._hass.async_create_background_task(
                self._async_apply_zones(ref), "harvia_ambilight_apply"
            )
            break  # one sauna device drives the zones

    async def _async_apply_zones(self, ref_temp: float) -> None:
        """Send the computed color to each active zone."""
        for zone in self._zones:
            if not zone.active:
                continue
            color = compute_zone_color(zone.curve, ref_temp, zone.offset)
            if color is None or color == zone.last_sent:
                continue
            targets = [
                e
                for e in zone.lights
                if (state := self._hass.states.get(e)) is not None
                and state.state == "on"
            ]
            if not targets:
                continue
            zone.last_sent = color
            await self._async_call_light(
                targets, {**color, "transition": AMBI_TRANSITION_SEC}
            )
            _LOGGER.debug(
                "Ambilight: %.1f°C (+%.1f offset) -> %s on %s",
                ref_temp,
                zone.offset,
                color,
                targets,
            )

    async def _async_call_light(
        self, targets: list[str], data: dict[str, Any]
    ) -> None:
        """light.turn_on with our own tracked context."""
        context = Context()
        self._own_context_ids.append(context.id)
        if len(self._own_context_ids) > MAX_TRACKED_CONTEXTS:
            self._own_context_ids = self._own_context_ids[-MAX_TRACKED_CONTEXTS:]
        await self._hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": targets, **data},
            blocking=False,
            context=context,
        )

    # ── Manual override detection ────────────────────────────────────

    def _matches_last_sent(self, entity_id: str, new_state) -> bool:
        """Check if a light's new color matches what we last sent its zone.

        Push integrations (Hue) report our own change with a fresh context,
        so we additionally compare values: hs within ±3°/±5%, kelvin ±75.
        """
        for zone in self._zones:
            if not zone.active or entity_id not in zone.lights:
                continue
            sent = zone.last_sent
            if sent is None:
                return False
            if "hs_color" in sent:
                hs = new_state.attributes.get("hs_color")
                if hs is None:
                    return False
                return (
                    abs(hs[0] - sent["hs_color"][0]) <= 3.0
                    and abs(hs[1] - sent["hs_color"][1]) <= 5.0
                )
            if "color_temp_kelvin" in sent:
                kelvin = new_state.attributes.get("color_temp_kelvin")
                if kelvin is None:
                    return False
                return abs(kelvin - sent["color_temp_kelvin"]) <= 75
        return False

    @callback
    def _handle_light_change(self, event: Event) -> None:
        """A zone light changed: detect manual color intervention."""
        if event.context and event.context.id in self._own_context_ids:
            return
        if self.manual_override or not self._session_was_active:
            return
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if (
            new_state is None
            or old_state is None
            or new_state.state != "on"
            or old_state.state != "on"
        ):
            return  # on/off toggles and availability are not color edits
        # Only a COLOR change counts as intervention (brightness is free)
        color_keys = ("hs_color", "color_temp_kelvin", "rgb_color", "xy_color")
        changed = any(
            new_state.attributes.get(k) != old_state.attributes.get(k)
            for k in color_keys
        )
        if not changed:
            return
        # Echo suppression: our own apply, reported back without our context
        entity_id = event.data.get("entity_id")
        if time.monotonic() - self._last_apply_ts < SELF_APPLY_GRACE_SEC:
            _LOGGER.debug(
                "Ambilight: color event on %s within self-apply grace — "
                "treated as echo",
                entity_id,
            )
            return
        if self._matches_last_sent(entity_id, new_state):
            _LOGGER.debug(
                "Ambilight: color on %s matches last sent value — echo",
                entity_id,
            )
            return
        # Is any device currently in an ambilight-driven session?
        data = self._coordinator.data
        if not data:
            return
        if any(
            (d._session_active or d._cooldown_active) and d.ambilight_enabled
            for d in data.devices.values()
        ):
            self.manual_override = True
            _LOGGER.info(
                "Ambilight: manual color change on %s detected — pausing "
                "until session end or switch toggle",
                event.data.get("entity_id"),
            )

    # ── Re-arm / restore ─────────────────────────────────────────────

    @callback
    def async_rearm(self) -> None:
        """Clear the manual override (called when the switch toggles on)."""
        self.manual_override = False
        self._last_ref_temp = None
        self._last_apply_ts = 0.0
        for zone in self._zones:
            zone.last_sent = None
        self._handle_coordinator_update()

    async def async_restore_standard(self) -> None:
        """Restore the everyday color + brightness on lit zone lights."""
        targets = [
            e
            for e in self.all_zone_lights
            if (state := self._hass.states.get(e)) is not None
            and state.state == "on"
        ]
        for zone in self._zones:
            zone.last_sent = None
        if not targets:
            return
        brightness = max(
            1, min(255, round(self._standard_brightness_pct * 255 / 100))
        )
        await self._async_call_light(
            targets,
            {
                "color_temp_kelvin": self._standard_kelvin,
                "brightness": brightness,
                "transition": AMBI_TRANSITION_SEC,
            },
        )
        _LOGGER.debug(
            "Ambilight: restored standard %dK / %d%% on %s",
            self._standard_kelvin,
            self._standard_brightness_pct,
            targets,
        )

    async def _handle_session_end(self, event: Event) -> None:
        """Session ended (incl. cooldown): back to the everyday standard."""
        if not self.configured:
            return
        device_id = event.data.get("device_id")
        data = self._coordinator.data
        if data and device_id not in data.devices:
            return
        await self.async_restore_standard()
