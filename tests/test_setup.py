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


class FakeApi(HarviaApiClientBase):
    """Offline stand-in for the Harvia cloud clients."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []

    async def async_authenticate(self) -> bool:
        return True

    async def async_get_user_data(self) -> dict:
        return {}

    async def async_get_devices(self) -> list[dict[str, Any]]:
        return [{"device_id": DEVICE_ID, "display_name": "Sauna"}]

    async def async_get_device_state(self, device_id: str) -> dict:
        return dict(XENIO_STATE)

    async def async_get_latest_device_data(self, device_id: str) -> dict:
        return dict(TELEMETRY)

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
    api = FakeApi()
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

    # Every platform produced at least one entity
    for platform in PLATFORMS:
        assert by_platform.get(platform.value), f"platform {platform.value} created no entities"

    # Every description key for this provider became an entity
    for domain, keys in _described_keys(provider).items():
        unique_ids = by_platform.get(domain, set())
        for key in keys:
            assert any(uid.endswith(f"_{key}") for uid in unique_ids), (
                f"{domain}.{key} described but not created for {provider}"
            )

    assert len(entries) >= 25


async def test_schedule_entities_only_for_xenio(hass: HomeAssistant) -> None:
    """The device-schedule sensor and switch are Xenio-only."""
    entry, _ = await _setup(hass, API_PROVIDER_HARVIAIO)
    registry = er.async_get(hass)
    uids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert not any(uid.endswith("_scheduled_start") for uid in uids)


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
