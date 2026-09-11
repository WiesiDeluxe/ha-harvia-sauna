"""Device-profile select tests (issue #9, Fenix only)."""
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.harvia_sauna.const import (
    API_PROVIDER_HARVIAIO,
    API_PROVIDER_MYHARVIA,
)

from .test_setup import _setup


async def test_profile_select_is_fenix_only(hass: HomeAssistant) -> None:
    """Xenio has no device profiles, so no select entity may appear."""
    entry, _ = await _setup(hass, API_PROVIDER_MYHARVIA)
    registry = er.async_get(hass)
    uids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert not any(uid.endswith("_profile_select") for uid in uids)
    assert not [s for s in hass.states.async_entity_ids("select")]


async def test_profile_select_uses_panel_names(hass: HomeAssistant) -> None:
    """Options are the panel's own names; the active one is selected."""
    await _setup(hass, API_PROVIDER_HARVIAIO)
    state = hass.states.get("select.sauna_heating_profile")
    assert state is not None, hass.states.async_entity_ids("select")
    assert state.attributes["options"] == ["Mild", "Gemütlich", "Intensiv", "Profile 3"]
    assert state.state == "Intensiv"  # activeProfile = 2
    assert state.attributes["target_temp"] == 95
    assert state.attributes["duration_min"] == 150


async def test_selecting_a_profile_writes_the_index(hass: HomeAssistant) -> None:
    """Selecting sends the profile index and reflects the new state."""
    _, api = await _setup(hass, API_PROVIDER_HARVIAIO)
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.sauna_heating_profile", "option": "Mild"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert api.writes[-1][1] == {"profile": "0"}
    assert hass.states.get("select.sauna_heating_profile").state == "Mild"


@pytest.mark.parametrize("provider", [API_PROVIDER_MYHARVIA, API_PROVIDER_HARVIAIO])
async def test_existing_entities_survive_the_upgrade(
    hass: HomeAssistant, provider: str
) -> None:
    """The new platform must not remove or rename anything that existed before.

    This is the promise of a non-breaking release: a user updating from 2.9.x
    keeps every entity, on both controllers.
    """
    entry, _ = await _setup(hass, provider)
    registry = er.async_get(hass)
    uids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    for key in ("_current_temperature", "_target_temperature", "_power", "_light"):
        assert any(uid.endswith(key) for uid in uids), f"{key} disappeared on {provider}"
    # climate keeps its own presets (unchanged on both providers)
    assert hass.states.get("climate.sauna_thermostat") is not None
