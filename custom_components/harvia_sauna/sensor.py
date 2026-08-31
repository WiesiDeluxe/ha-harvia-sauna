"""Sensor platform for Harvia Sauna."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import API_PROVIDER_HARVIAIO, API_PROVIDER_MYHARVIA, CONF_API_PROVIDER, DOMAIN
from .coordinator import decode_status_bits, decode_timed_start, schedule_state, HarviaDeviceData, HarviaSaunaCoordinator
from .entity import HarviaBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HarviaSensorDescription(SensorEntityDescription):
    """Describe a Harvia sensor entity."""

    value_fn: Callable[[HarviaDeviceData], int | float | str | None]
    providers: tuple[str, ...] | None = None  # None = all providers
    attrs_fn: Callable[[HarviaDeviceData], dict | None] | None = None


SENSOR_DESCRIPTIONS: list[HarviaSensorDescription] = [
    HarviaSensorDescription(
        key="current_temperature",
        translation_key="current_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        value_fn=lambda d: d.current_temp,
    ),
    # v2.7.0: Time-to-ready estimation from the reference temp trend
    HarviaSensorDescription(
        key="time_to_ready",
        translation_key="time_to_ready",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-sand",
        value_fn=lambda d: d.time_to_ready_min,
    ),
    HarviaSensorDescription(
        key="ready_at",
        translation_key="ready_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check-outline",
        value_fn=lambda d: d.ready_at,
    ),
    # ── Device-held schedule (v2.9.0, Xenio timedStart) ───────────
    HarviaSensorDescription(
        key="scheduled_start",
        translation_key="scheduled_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-clock",
        providers=("xenio",),
        value_fn=lambda d: schedule_state(d)[0],
        attrs_fn=lambda d: schedule_state(d)[1],
    ),
    # ── Smart Preheat & Statistics (v2.8.0) ──────────────────────
    HarviaSensorDescription(
        key="planned_start",
        translation_key="planned_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-start",
        value_fn=lambda d: None,  # filled by coordinator-level sensor below
    ),
    HarviaSensorDescription(
        key="last_session_energy",
        translation_key="last_session_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:lightning-bolt",
        value_fn=lambda d: d.last_session_kwh,
    ),
    HarviaSensorDescription(
        key="sessions_week",
        translation_key="sessions_week",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:calendar-week",
        value_fn=lambda d: d.sessions_week,
    ),
    HarviaSensorDescription(
        key="records",
        translation_key="records",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:trophy",
        value_fn=lambda d: d.total_sessions_local,
    ),
    HarviaSensorDescription(
        key="humidity",
        translation_key="humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.humidity,
    ),
    HarviaSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        icon="mdi:thermometer-chevron-up",
        value_fn=lambda d: d.target_temp,
    ),
    HarviaSensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:timer-sand",
        value_fn=lambda d: d.remaining_time if d.active else 0,
    ),
    HarviaSensorDescription(
        key="heat_up_time",
        translation_key="heat_up_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:timer-alert",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.heat_up_time,
    ),
    HarviaSensorDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.wifi_rssi,
    ),
    HarviaSensorDescription(
        key="status_codes",
        translation_key="status_codes",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:information-outline",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.status_codes,
        # Decoded bit map + device-held schedule (issue #6 community research)
        attrs_fn=lambda d: {
            **decode_status_bits(d.status_codes),
            "timed_start": decode_timed_start(d.timed_start),
        },
    ),
    HarviaSensorDescription(
        key="aroma_level",
        translation_key="aroma_level",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:flower",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.aroma_level,
    ),
    # Diagnostic counters (Lifetime values)
    HarviaSensorDescription(
        key="ph1_relay_counter",
        translation_key="ph1_relay_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            d.ph1_relay_counter_lt if d.ph1_relay_counter_lt > 0
            else (d.ph1_relay_counter if d.ph1_relay_counter > 0 else None)
        ),
    ),
    HarviaSensorDescription(
        key="ph2_relay_counter",
        translation_key="ph2_relay_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            d.ph2_relay_counter_lt if d.ph2_relay_counter_lt > 0
            else (d.ph2_relay_counter if d.ph2_relay_counter > 0 else None)
        ),
    ),
    HarviaSensorDescription(
        key="ph3_relay_counter",
        translation_key="ph3_relay_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            d.ph3_relay_counter_lt if d.ph3_relay_counter_lt > 0
            else (d.ph3_relay_counter if d.ph3_relay_counter > 0 else None)
        ),
    ),
    HarviaSensorDescription(
        key="heat_on_counter",
        translation_key="heat_on_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            d.heat_on_counter_lt if d.heat_on_counter_lt > 0
            else (d.heat_on_counter if d.heat_on_counter > 0 else None)
        ),
    ),
    HarviaSensorDescription(
        key="steam_on_counter",
        translation_key="steam_on_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            d.steam_on_counter_lt if d.steam_on_counter_lt > 0
            else (d.steam_on_counter if d.steam_on_counter > 0 else None)
        ),
    ),
    HarviaSensorDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        value_fn=lambda d: d.heater_power if d.heat_on else 0,
    ),
    HarviaSensorDescription(
        key="energy",
        translation_key="energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
        value_fn=lambda d: d.energy_kwh,
    ),
    # New Fenix-specific sensors
    HarviaSensorDescription(
        key="heater_power_actual",
        translation_key="heater_power_actual",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        providers=(API_PROVIDER_HARVIAIO,),
        value_fn=lambda d: d.heater_power_actual if d.heater_power_actual > 0 else None,
    ),
    HarviaSensorDescription(
        key="main_sensor_temp",
        translation_key="main_sensor_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        providers=(API_PROVIDER_HARVIAIO,),
        value_fn=lambda d: d.main_sensor_temp,
    ),
    HarviaSensorDescription(
        key="ext_sensor_temp",
        translation_key="ext_sensor_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-low",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        providers=(API_PROVIDER_HARVIAIO,),
        value_fn=lambda d: d.ext_sensor_temp,
    ),
    HarviaSensorDescription(
        key="panel_temp",
        translation_key="panel_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-lines",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        providers=(API_PROVIDER_HARVIAIO,),
        value_fn=lambda d: d.panel_temp,
    ),
    HarviaSensorDescription(
        key="total_sessions",
        translation_key="total_sessions",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        providers=(API_PROVIDER_HARVIAIO,),
        value_fn=lambda d: d.total_sessions if d.total_sessions > 0 else None,
    ),
    HarviaSensorDescription(
        key="total_bathing_hours",
        translation_key="total_bathing_hours",
        native_unit_of_measurement="h",
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:clock-time-eight",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        providers=(API_PROVIDER_HARVIAIO,),
        value_fn=lambda d: d.total_bathing_hours if d.total_bathing_hours > 0 else None,
    ),
    HarviaSensorDescription(
        key="total_hours",
        translation_key="total_hours",
        native_unit_of_measurement="h",
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:clock-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        providers=(API_PROVIDER_HARVIAIO,),
        value_fn=lambda d: d.total_hours if d.total_hours > 0 else None,
    ),
    # Active profile status (read-only)
    HarviaSensorDescription(
        key="active_profile",
        translation_key="active_profile",
        icon="mdi:tune",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        providers=(API_PROVIDER_HARVIAIO,),
        value_fn=lambda d: d.active_profile if d.active_profile >= 0 else None,
    ),
    # Session tracking
    HarviaSensorDescription(
        key="last_session_duration",
        translation_key="last_session_duration",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:timer-check",
        value_fn=lambda d: d.last_session_duration if d.last_session_duration > 0 else None,
    ),
    HarviaSensorDescription(
        key="last_session_max_temp",
        translation_key="last_session_max_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        icon="mdi:thermometer-high",
        value_fn=lambda d: d.last_session_max_temp if d.last_session_max_temp > 0 else None,
    ),
    HarviaSensorDescription(
        key="sessions_today",
        translation_key="sessions_today",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: d.sessions_today,
    ),
    HarviaSensorDescription(
        key="temp_trend",
        translation_key="temp_trend",
        native_unit_of_measurement="°C/min",
        icon="mdi:trending-up",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.temp_trend,
    ),
]


RESTORABLE_SESSION_KEYS = {"last_session_duration", "last_session_max_temp", "sessions_today"}
RESTORABLE_STATS_KEYS = {"last_session_energy", "sessions_week", "records"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Harvia sensor entities."""
    coordinator: HarviaSaunaCoordinator = hass.data[DOMAIN][entry.entry_id]
    provider = entry.data.get(CONF_API_PROVIDER, API_PROVIDER_MYHARVIA)

    entities = []
    for device_id in coordinator.data.devices:
        for description in SENSOR_DESCRIPTIONS:
            # Skip entities not matching the configured API provider
            if description.providers is not None and provider not in description.providers:
                continue

            if description.key == "energy":
                entities.append(
                    HarviaEnergySensor(coordinator, device_id, description)
                )
            elif description.key == "planned_start":
                entities.append(
                    HarviaPlannedStartSensor(coordinator, device_id, description)
                )
            elif description.key in RESTORABLE_SESSION_KEYS:
                entities.append(
                    HarviaSessionSensor(coordinator, device_id, description)
                )
            elif description.key in RESTORABLE_STATS_KEYS:
                entities.append(
                    HarviaStatsSensor(coordinator, device_id, description)
                )
            else:
                entities.append(
                    HarviaSensor(coordinator, device_id, description)
                )

    async_add_entities(entities)


class HarviaSensor(HarviaBaseEntity, SensorEntity):
    """Harvia Sauna sensor entity."""

    entity_description: HarviaSensorDescription

    def __init__(
        self,
        coordinator: HarviaSaunaCoordinator,
        device_id: str,
        description: HarviaSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | float | str | None:
        """Return the sensor value."""
        device = self._get_device_data()
        if device is None:
            return None
        return self.entity_description.value_fn(device)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return description-provided attributes, if any."""
        fn = getattr(self.entity_description, "attrs_fn", None)
        if fn is None:
            return None
        device = self._get_device_data()
        return fn(device) if device else None


class HarviaEnergySensor(HarviaSensor, RestoreEntity):
    """Energy sensor with state restoration across HA restarts."""

    async def async_added_to_hass(self) -> None:
        """Restore last known energy value on startup."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in ("unknown", "unavailable"):
            return

        try:
            restored_value = float(last_state.state)
        except (ValueError, TypeError):
            return

        # Write restored value back to coordinator device data
        device = self._get_device_data()
        if device is not None and restored_value > device.energy_kwh:
            device.energy_kwh = restored_value
            _LOGGER.debug(
                "Restored energy value: %.3f kWh", restored_value
            )


class HarviaSessionSensor(HarviaSensor, RestoreEntity):
    """Session sensor with state restoration across HA restarts/reloads."""

    async def async_added_to_hass(self) -> None:
        """Restore last known session value on startup."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in ("unknown", "unavailable"):
            return

        try:
            restored_value = float(last_state.state)
        except (ValueError, TypeError):
            return

        device = self._get_device_data()
        if device is None:
            return

        key = self.entity_description.key

        if key == "last_session_duration" and device.last_session_duration == 0.0:
            device.last_session_duration = restored_value
            _LOGGER.debug("Restored last_session_duration: %.1f min", restored_value)

        elif key == "last_session_max_temp" and device.last_session_max_temp == 0.0:
            device.last_session_max_temp = restored_value
            _LOGGER.debug("Restored last_session_max_temp: %.0f°C", restored_value)

        elif key == "sessions_today" and device.sessions_today == 0:
            # Only restore if same day (otherwise the midnight reset is correct)
            import datetime as dt
            today = dt.date.today().isoformat()
            if device._sessions_today_date == "" or device._sessions_today_date == today:
                device.sessions_today = int(restored_value)
                device._sessions_today_date = today
                _LOGGER.debug("Restored sessions_today: %d", int(restored_value))


class HarviaPlannedStartSensor(HarviaSensor):
    """Computed heater start time for the active preheat schedule.

    Coordinator-level value (the scheduler lives on the coordinator, not on
    device data), so the value is read from coordinator.preheat. Attributes
    expose the scheduled ready-at time and the heating-model calibration
    state for transparency.
    """

    @property
    def native_value(self):
        """Return the planned start time, or None when nothing scheduled."""
        scheduler = getattr(self.coordinator, "preheat", None)
        if scheduler is None:
            return None
        return scheduler.planned_start

    @property
    def extra_state_attributes(self) -> dict | None:
        """Expose schedule and model details."""
        scheduler = getattr(self.coordinator, "preheat", None)
        model = getattr(self.coordinator, "heating_model", None)
        attrs: dict = {}
        if scheduler is not None:
            ready_at = scheduler.ready_at
            attrs["ready_at"] = ready_at.isoformat() if ready_at else None
            attrs["buffer_min"] = scheduler.buffer_min
        if model is not None:
            attrs["model_calibrated"] = model.calibrated
            attrs["model_samples"] = model.samples_total
        return attrs or None


class HarviaStatsSensor(HarviaSensor, RestoreEntity):
    """Statistics sensor with state + attribute restoration.

    Restores last_session_energy, sessions_week (with its ISO-week anchor)
    and records (with hottest/longest attributes) so the values survive
    restarts and reloads.
    """

    async def async_added_to_hass(self) -> None:
        """Restore the persisted statistic value and attributes."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in ("unknown", "unavailable"):
            return

        device = self._get_device_data()
        if device is None:
            return

        key = self.entity_description.key
        attrs = last_state.attributes

        try:
            value = float(last_state.state)
        except (ValueError, TypeError):
            value = None

        if key == "last_session_energy" and device.last_session_kwh is None:
            # Sanity bound: a single session is realistically < 50 kWh. A
            # larger restored value stems from an unreliable start snapshot
            # (e.g. a cold start where the meter read 0) — don't restore it.
            if value is not None and 0.0 <= value < 50.0:
                device.last_session_kwh = value

        elif key == "sessions_week" and device.sessions_week == 0:
            # Only restore within the same ISO week
            import datetime as dt
            iso = dt.datetime.now().isocalendar()
            week_anchor = f"{iso[0]}-{iso[1]:02d}"
            restored_anchor = attrs.get("week_anchor")
            if restored_anchor == week_anchor and value is not None:
                device.sessions_week = int(value)
                device._sessions_week_anchor = week_anchor

        elif key == "records":
            if value is not None and device.total_sessions_local == 0:
                device.total_sessions_local = int(value)
            if device.record_max_temp is None:
                rec_t = attrs.get("hottest_session_c")
                if rec_t is not None:
                    device.record_max_temp = float(rec_t)
            if device.record_duration_min is None:
                rec_d = attrs.get("longest_session_min")
                if rec_d is not None:
                    device.record_duration_min = float(rec_d)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Expose record details and the week anchor for restoration."""
        device = self._get_device_data()
        if device is None:
            return None
        key = self.entity_description.key
        if key == "records":
            return {
                "hottest_session_c": device.record_max_temp,
                "longest_session_min": device.record_duration_min,
                "total_sessions": device.total_sessions_local,
            }
        if key == "sessions_week":
            return {"week_anchor": device._sessions_week_anchor or None}
        return None
