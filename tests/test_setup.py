"""Integration setup tests.

These tests set up the integration against a fake cloud client and verify
that every platform loads and every described entity is created. They exist
because the platform wiring (constructor signatures, name collisions,
provider filters, missing imports) cannot be verified by pure logic tests —
each of those failure modes shipped once before this harness existed.
"""
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.harvia_sauna import PLATFORMS
from custom_components.harvia_sauna.api_base import HarviaApiClientBase
from custom_components.harvia_sauna.const import (
    API_PROVIDER_HARVIAIO,
    API_PROVIDER_MYHARVIA,
    CONF_API_PROVIDER,
    CONF_HEATER_MODEL,
    CONF_HEATER_POWER,
    DOMAIN,
)

DEVICE_ID = "d589822d-be66-402c-8c50-c99e3d461323"

# A real Xenio CX110 shadow (idle, no schedule), trimmed to the fields the
# coordinator consumes. Values are from a captured device.
XENIO_STATE: dict[str, Any] = {
    "active": 0, "light": 0, "fan": 0, "steamEn": 0, "targetTemp": 62,
    "targetRh": 0, "heatUpTime": 60, "onTime": 180, "dehumEn": 0,
    "autoLight": 1, "tempUnit": "C", "timedStart": "AAAAAAAAAAA=",
    "displayName": "Sauna", "autoFan": 0, "aromaEn": 0, "aromaLevel": 0,
    "wClkEn": 0, "wClk": "", "maxOnTime": 6, "maxTemp": 110, "minTemp": 40,
    "statusCodes": 4108, "errorCodes": 0, "swVer": "2.3.4", "online": 1,
    "expired": False,
}
TELEMETRY: dict[str, Any] = {"temperature": 25, "humidity": 30, "heatOn": 0}

# Fenix panels additionally carry their own profile definitions (issue #9).
FENIX_PROFILES: dict[str, Any] = {
    "0": {"name": "Mild", "targetTemp": 45, "targetHum": 45, "duration": 150,
          "heater": {"on": 1}, "steamer": {"on": 0}, "light": {"on": 0}},
    "1": {"name": "Gemütlich", "targetTemp": 65, "targetHum": 40, "duration": 120,
          "heater": {"on": 1}, "steamer": {"on": 1}, "light": {"on": 0}},
    "2": {"name": "Intensiv", "targetTemp": 95, "targetHum": 10, "duration": 150,
          "heater": {"on": 1}, "steamer": {"on": 0}, "light": {"on": 0}},
    "3": {"name": "", "targetTemp": 95, "targetHum": 0, "duration": 150,
          "heater": {"on": 1}, "steamer": {"on": 1}, "light": {"on": 0}},
}


class FakeApi(HarviaApiClientBase):
    """Offline stand-in for the Harvia cloud clients."""

    def __init__(self, provider: str = API_PROVIDER_MYHARVIA) -> None:
        self.writes: list[tuple[str, dict]] = []
        self.provider = provider
        # Mirrors the real clients: only the Fenix cloud refuses a duration
        self.supports_session_duration = provider != API_PROVIDER_HARVIAIO
        self.active_profile = 2
        self._raw_state: dict[str, Any] = {}
        self._raw_telemetry: dict[str, Any] = {}
        self.state_overrides: dict[str, Any] = {}
        self.telemetry_overrides: dict[str, Any] = {}

    @property
    def last_raw_state(self) -> dict[str, Any]:
        return self._raw_state

    @property
    def last_raw_telemetry(self) -> dict[str, Any]:
        return self._raw_telemetry

    async def async_authenticate(self) -> bool:
        return True

    async def async_get_user_data(self) -> dict:
        return {}

    async def async_get_devices(self) -> list[dict[str, Any]]:
        return [{"device_id": DEVICE_ID, "display_name": "Sauna"}]

    def __init_subclass__(cls, **kw):  # pragma: no cover
        super().__init_subclass__(**kw)

    async def async_get_device_state(self, device_id: str) -> dict:
        state = dict(XENIO_STATE)
        if self.provider == API_PROVIDER_HARVIAIO:
            # Fenix reports named fields, no Xenio-style statusCodes bit field
            state.pop("statusCodes", None)
            state["profiles"] = {k: dict(v) for k, v in FENIX_PROFILES.items()}
            state["activeProfile"] = self.active_profile
            state["saunaStatus"] = 0
        state.update(self.state_overrides)
        self._raw_state[device_id] = state
        return state

    async def async_set_active_profile(self, device_id: str, index: int) -> None:
        self.writes.append((device_id, {"profile": str(index)}))
        self.active_profile = index

    async def async_get_latest_device_data(self, device_id: str) -> dict:
        data = dict(TELEMETRY)
        data.update(self.telemetry_overrides)
        self._raw_telemetry[device_id] = data
        return data

    async def async_request_state_change(self, device_id: str, payload: dict, *a, **kw) -> None:
        self.writes.append((device_id, payload))

    async def async_start_push_updates(self, *a, **kw) -> None:
        return None

    async def async_stop_push_updates(self) -> None:
        return None

    @property
    def push_connected(self) -> bool:
        return False

    @property
    def push_connections_info(self) -> list[dict[str, Any]]:
        return []


async def _setup(hass: HomeAssistant, provider: str) -> tuple[MockConfigEntry, FakeApi]:
    api = FakeApi(provider)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_API_PROVIDER: provider,
            CONF_HEATER_MODEL: "other",
            CONF_HEATER_POWER: 10800,
        },
        options={},
        unique_id="user@example.com",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.harvia_sauna.create_api_client", return_value=api):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, api


def _described_keys(provider: str) -> dict[str, set[str]]:
    """Every description key each platform promises for this provider."""
    from custom_components.harvia_sauna.binary_sensor import BINARY_SENSOR_DESCRIPTIONS
    from custom_components.harvia_sauna.number import NUMBER_DESCRIPTIONS
    from custom_components.harvia_sauna.sensor import SENSOR_DESCRIPTIONS
    from custom_components.harvia_sauna.switch import SWITCH_DESCRIPTIONS

    def keys(descs):
        return {
            d.key for d in descs
            if getattr(d, "providers", None) is None or provider in d.providers
        }

    return {
        "sensor": keys(SENSOR_DESCRIPTIONS),
        "binary_sensor": keys(BINARY_SENSOR_DESCRIPTIONS),
        "number": keys(NUMBER_DESCRIPTIONS),
        "switch": keys(SWITCH_DESCRIPTIONS),
    }


@pytest.mark.parametrize("provider", [API_PROVIDER_MYHARVIA, API_PROVIDER_HARVIAIO])
async def test_all_platforms_load_and_every_described_entity_exists(
    hass: HomeAssistant, provider: str
) -> None:
    """Setup must load every platform and create every described entity."""
    entry, _ = await _setup(hass, provider)
    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    by_platform: dict[str, set[str]] = {}
    for e in entries:
        by_platform.setdefault(e.domain, set()).add(e.unique_id)

    # Every platform produced at least one entity, except platforms that are
    # deliberately gated to one controller family (select = Fenix profiles).
    gated = {"select"} if provider != API_PROVIDER_HARVIAIO else set()
    for platform in PLATFORMS:
        if platform.value in gated:
            assert not by_platform.get(platform.value), (
                f"{platform.value} must not create entities for {provider}"
            )
            continue
        assert by_platform.get(platform.value), f"platform {platform.value} created no entities"

    # Every description key for this provider became an entity
    for domain, keys in _described_keys(provider).items():
        unique_ids = by_platform.get(domain, set())
        for key in keys:
            assert any(uid.endswith(f"_{key}") for uid in unique_ids), (
                f"{domain}.{key} described but not created for {provider}"
            )

    assert len(entries) >= 25


@pytest.mark.parametrize("provider", [API_PROVIDER_MYHARVIA, API_PROVIDER_HARVIAIO])
async def test_every_entity_can_be_enabled_and_gets_a_state(
    hass: HomeAssistant, provider: str
) -> None:
    """Entities disabled by default must still be valid once a user enables them.

    A registry entry alone proves nothing: HA validates an entity (e.g. the
    sensor-with-config-category rule) only when it is actually added to the
    state machine, which never happens for disabled-by-default entities in
    the setup test above. Enable everything, reload, and require a state.
    """
    entry, api = await _setup(hass, provider)
    registry = er.async_get(hass)
    for e in er.async_entries_for_config_entry(registry, entry.entry_id):
        if e.disabled:
            registry.async_update_entity(e.entity_id, disabled_by=None)
    with patch("custom_components.harvia_sauna.create_api_client", return_value=api):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    missing = [
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if hass.states.get(e.entity_id) is None
    ]
    assert not missing, f"enabled entities without a state ({provider}): {missing}"


async def test_schedule_arm_switch_is_xenio_only(hass: HomeAssistant) -> None:
    """Arming/disarming is a Xenio concept (timedStart byte 0).

    Fenix stores its schedule as plain timestamps with no enable flag, so it
    gets the read-only schedule sensor but no arm switch.
    """
    entry, _ = await _setup(hass, API_PROVIDER_HARVIAIO)
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    switch_uids = {e.unique_id for e in entries if e.domain == "switch"}
    sensor_uids = {e.unique_id for e in entries if e.domain == "sensor"}
    assert not any(uid.endswith("_scheduled_start") for uid in switch_uids)
    assert any(uid.endswith("_scheduled_start") for uid in sensor_uids)


async def test_schedule_services_write_expected_bytes(hass: HomeAssistant) -> None:
    """set_schedule / clear_schedule produce the measured timedStart bytes."""
    _, api = await _setup(hass, API_PROVIDER_MYHARVIA)
    await hass.services.async_call(
        DOMAIN, "set_schedule",
        {"device_id": DEVICE_ID, "ready_at": "2026-09-01T18:00:00+02:00",
         "duration": 120, "target_temp": 62, "enabled": True},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "clear_schedule", {"device_id": DEVICE_ID}, blocking=True
    )
    assert [p for _, p in api.writes] == [
        {"timedStart": "AQg+AID2lmo="},   # 01 08 3e 00 80 f6 96 6a = armed, 120 min, 62 C, 2026-09-01T16:00Z
        {"timedStart": "AAAAAAAAAAA="},
    ]
