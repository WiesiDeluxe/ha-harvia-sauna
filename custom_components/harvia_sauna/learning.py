"""Heating model: learn the heater's temperature-dependent heating rate.

The heating rate of a sauna is NOT constant over the temperature range —
stone-heavy heaters launch slowly (the stones absorb the first minutes of
energy), peak mid-range and flatten near the top. A single average rate
would be far off at both ends, so the model learns a rate per 5 °C
temperature bucket via an exponentially weighted moving average (EWMA).

Learning rules (per bucket, per heat-up):
- heater must be actively heating throughout the bucket
- the door must stay closed (open-door buckets learn ventilation, not
  the heater)
- no in-bucket dip larger than HEATING_DIP_TOLERANCE_C (thermostat
  cycling / disturbances)
- the bucket must lie below target − HEATING_LEARN_CEILING_BELOW_TARGET_C
  (above that the thermostat regulates — you would measure regulation,
  not heater capacity)
- the resulting rate must be within [HEATING_RATE_MIN, HEATING_RATE_MAX]

Estimation sums learned bucket times from start to target temperature,
pro-rata for partial buckets. Buckets never observed fall back to the
seed profile for the configured heater class; buckets beyond all known
data extrapolate from the last known rate with a decay per bucket.

Seeds are derived from measured data (Legend 10.8 kW / ~100 kg:
launch ~0.3 °C/min, mid ~0.82 °C/min, ~0.7 °C/min above 65 °C) plus
published heat-up figures for low-mass heaters (KIP class: 74 °C in
30–40 min). The model self-corrects after the first real heat-up.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DEFAULT_HEATER_CLASS,
    HEATER_CLASS_HIGH_MASS,
    HEATER_CLASS_LOW_MASS,
    HEATER_CLASS_MEDIUM_MASS,
    HEATER_MODEL_CLASSES,
    HEATING_BUCKET_MAX_C,
    HEATING_BUCKET_MIN_C,
    HEATING_BUCKET_SIZE_C,
    HEATING_DIP_TOLERANCE_C,
    HEATING_EWMA_ALPHA,
    HEATING_EXTRAPOLATION_DECAY,
    HEATING_LEARN_CEILING_BELOW_TARGET_C,
    HEATING_RATE_MAX,
    HEATING_RATE_MIN,
    STORAGE_KEY_HEATING_MODEL,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def _seed_rate(bucket_floor: int, heater_class: str) -> float:
    """Return the seed heating rate (°C/min) for a bucket and class.

    Shape per class (mid_rate × launch ramp × top flattening); the
    high-mass curve mirrors the measured Legend reference.
    """
    mid = {
        HEATER_CLASS_HIGH_MASS: 0.82,
        HEATER_CLASS_MEDIUM_MASS: 1.0,
        HEATER_CLASS_LOW_MASS: 1.4,
    }.get(heater_class, 1.0)
    launch_factor = {
        HEATER_CLASS_HIGH_MASS: 0.37,
        HEATER_CLASS_MEDIUM_MASS: 0.5,
        HEATER_CLASS_LOW_MASS: 0.7,
    }.get(heater_class, 0.5)

    if bucket_floor < 25:
        return round(mid * launch_factor, 3)
    if bucket_floor < 30:
        return round(mid * 0.75, 3)
    if bucket_floor < 65:
        return round(mid, 3)
    if bucket_floor < 70:
        return round(mid * 0.85, 3)
    if bucket_floor < 80:
        return round(mid * 0.6, 3)
    return round(mid * 0.45, 3)


def heater_class_for_model(model_key: str | None) -> str:
    """Map a configured heater model key to a stone-mass class."""
    if not model_key:
        return DEFAULT_HEATER_CLASS
    return HEATER_MODEL_CLASSES.get(model_key, DEFAULT_HEATER_CLASS)


class HeatingModel:
    """Per-config-entry learned heating model with persistence."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, heater_class: str
    ) -> None:
        """Initialize the model."""
        self._hass = hass
        self._heater_class = heater_class
        self._store: Store = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_HEATING_MODEL}_{entry_id}"
        )
        # bucket_floor (int) -> learned EWMA rate in °C/min
        self._learned: dict[int, float] = {}
        self._samples_total = 0
        # Live recording of the current heat-up:
        # bucket_floor -> {"enter_ts": monotonic, "min_temp": float,
        #                  "max_temp": float, "dirty": bool}
        self._recording: dict[int, dict[str, Any]] = {}
        self._last_temp: float | None = None
        self._last_bucket: int | None = None

    # ── Persistence ──────────────────────────────────────────────────

    async def async_load(self) -> None:
        """Load learned buckets from the HA store."""
        data = await self._store.async_load()
        if data:
            self._learned = {
                int(k): float(v) for k, v in data.get("buckets", {}).items()
            }
            self._samples_total = int(data.get("samples_total", 0))
            _LOGGER.debug(
                "Heating model loaded: %d learned buckets, %d samples",
                len(self._learned),
                self._samples_total,
            )

    async def async_save(self) -> None:
        """Persist learned buckets."""
        await self._store.async_save(
            {
                "buckets": {str(k): v for k, v in self._learned.items()},
                "samples_total": self._samples_total,
                "heater_class": self._heater_class,
            }
        )

    # ── Properties for diagnostics / sensor attributes ───────────────

    @property
    def calibrated(self) -> bool:
        """Return True once at least one real heat-up was learned."""
        return bool(self._learned)

    @property
    def learned_buckets(self) -> dict[int, float]:
        """Return a copy of the learned buckets."""
        return dict(self._learned)

    @property
    def samples_total(self) -> int:
        """Return the number of learned bucket samples."""
        return self._samples_total

    # ── Live recording during a heat-up ──────────────────────────────

    @staticmethod
    def _bucket_floor(temp_c: float) -> int:
        return int(temp_c // HEATING_BUCKET_SIZE_C) * HEATING_BUCKET_SIZE_C

    def reset_recording(self) -> None:
        """Drop the in-progress recording (session start / abort)."""
        self._recording.clear()
        self._last_temp = None
        self._last_bucket = None

    def add_sample(
        self,
        temp_c: float,
        heating: bool,
        door_open: bool | None,
        target_temp: float | None,
        now: float | None = None,
    ) -> None:
        """Feed a reference temperature sample during a session.

        Tracks bucket entry timestamps and marks buckets dirty when a
        learning rule is violated. Completed buckets are scored on the
        fly so an aborted session still keeps its clean buckets.
        """
        if now is None:
            now = time.monotonic()

        if not heating:
            # Heater off (thermostat or session end): current bucket is
            # no longer a pure heat-up measurement
            if self._last_bucket is not None:
                rec = self._recording.get(self._last_bucket)
                if rec is not None:
                    rec["dirty"] = True
            return

        if temp_c < HEATING_BUCKET_MIN_C or temp_c > HEATING_BUCKET_MAX_C:
            return

        bucket = self._bucket_floor(temp_c)
        rec = self._recording.get(bucket)
        if rec is None:
            rec = self._recording[bucket] = {
                "enter_ts": now,
                "enter_temp": temp_c,
                "min_temp": temp_c,
                "max_temp": temp_c,
                "dirty": False,
            }

        # Dirty conditions
        if door_open:
            rec["dirty"] = True
        if temp_c < rec["max_temp"] - HEATING_DIP_TOLERANCE_C:
            rec["dirty"] = True
        if (
            target_temp is not None
            and bucket + HEATING_BUCKET_SIZE_C
            > target_temp - HEATING_LEARN_CEILING_BELOW_TARGET_C
        ):
            rec["dirty"] = True  # thermostat regulation zone

        rec["min_temp"] = min(rec["min_temp"], temp_c)
        rec["max_temp"] = max(rec["max_temp"], temp_c)

        # Bucket transition upwards: score the bucket we just left
        if self._last_bucket is not None and bucket > self._last_bucket:
            self._score_bucket(self._last_bucket, exit_ts=now, exit_temp=temp_c)
        self._last_bucket = bucket
        self._last_temp = temp_c

    def _score_bucket(
        self, bucket: int, exit_ts: float, exit_temp: float
    ) -> None:
        """Convert a completed bucket recording into a learned rate."""
        rec = self._recording.pop(bucket, None)
        if rec is None or rec["dirty"]:
            return
        span_c = exit_temp - rec["enter_temp"]
        elapsed_min = (exit_ts - rec["enter_ts"]) / 60.0
        if elapsed_min <= 0 or span_c <= 0:
            return
        rate = span_c / elapsed_min
        if not HEATING_RATE_MIN <= rate <= HEATING_RATE_MAX:
            _LOGGER.debug(
                "Heating model: bucket %d rate %.2f out of bounds — discarded",
                bucket,
                rate,
            )
            return
        previous = self._learned.get(bucket)
        if previous is None:
            self._learned[bucket] = round(rate, 3)
        else:
            self._learned[bucket] = round(
                HEATING_EWMA_ALPHA * rate
                + (1 - HEATING_EWMA_ALPHA) * previous,
                3,
            )
        self._samples_total += 1
        _LOGGER.debug(
            "Heating model: bucket %d learned %.2f °C/min (EWMA %.2f, n=%d)",
            bucket,
            rate,
            self._learned[bucket],
            self._samples_total,
        )

    def finish_recording(self) -> bool:
        """End of heat-up: discard incomplete buckets. Returns True if
        anything was learned this run (caller should persist)."""
        learned_before = self._samples_total
        self._recording.clear()
        self._last_bucket = None
        self._last_temp = None
        return self._samples_total > learned_before

    # ── Estimation ───────────────────────────────────────────────────

    def rate_for_bucket(self, bucket_floor: int) -> tuple[float, str]:
        """Return (rate °C/min, source) for a bucket.

        Source is one of "learned", "seed", "extrapolated".
        """
        if bucket_floor in self._learned:
            return self._learned[bucket_floor], "learned"
        # Extrapolate above the highest known bucket from its rate
        if self._learned:
            highest = max(self._learned)
            if bucket_floor > highest:
                steps = (bucket_floor - highest) // HEATING_BUCKET_SIZE_C
                rate = self._learned[highest] * (
                    HEATING_EXTRAPOLATION_DECAY**steps
                )
                return max(rate, HEATING_RATE_MIN), "extrapolated"
        return _seed_rate(bucket_floor, self._heater_class), "seed"

    def estimate_minutes(
        self, start_temp_c: float, target_temp_c: float
    ) -> float | None:
        """Estimate heat-up duration in minutes (no buffer included)."""
        if target_temp_c <= start_temp_c:
            return 0.0
        start = max(start_temp_c, float(HEATING_BUCKET_MIN_C))
        if start >= target_temp_c:
            return 0.0

        total_min = 0.0
        temp = start
        while temp < target_temp_c:
            bucket = self._bucket_floor(temp)
            bucket_end = bucket + HEATING_BUCKET_SIZE_C
            segment_end = min(bucket_end, target_temp_c)
            span = segment_end - temp
            rate, _ = self.rate_for_bucket(bucket)
            if rate <= 0:
                return None
            total_min += span / rate
            temp = segment_end
        return round(total_min, 1)
