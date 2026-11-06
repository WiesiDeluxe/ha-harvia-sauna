"""The learned heating model (#17, J0zefG).

- finish_recording() compared the sample counter with itself, so the model was
  never saved and every restart went back to the seed rates (since 2.8.0).
- The estimate started at 10 °C at the coldest, so a colder cabin was ready late.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.harvia_sauna.const import DOMAIN, STORAGE_KEY_HEATING_MODEL
from custom_components.harvia_sauna.learning import (
    HEATER_CLASS_MEDIUM_MASS,
    HeatingModel,
)

from .test_availability_and_unload import REF
from .test_ext_sensor_grace import _cooldown


def _model(hass: HomeAssistant, entry_id: str = "entry") -> HeatingModel:
    return HeatingModel(hass, entry_id, HEATER_CLASS_MEDIUM_MASS)


def _heat_up(model: HeatingModel, first: int = 20, last: int = 40) -> None:
    """1 °C per minute, far below the 90 °C target."""
    model.reset_recording()
    for minute, temp in enumerate(range(first, last)):
        model.add_sample(
            float(temp), heating=True, door_open=False, target_temp=90.0,
            now=minute * 60.0,
        )


async def test_a_learned_heat_up_survives_a_restart(
    hass: HomeAssistant, hass_storage
) -> None:
    model = _model(hass)
    _heat_up(model)
    assert model.samples_total
    assert model.finish_recording(), "the caller saves only on True"
    await model.async_save()

    restarted = _model(hass)
    await restarted.async_load()
    assert restarted.calibrated
    assert restarted.samples_total == model.samples_total
    assert restarted.learned_buckets == model.learned_buckets


async def test_nothing_new_means_nothing_to_save(hass: HomeAssistant, hass_storage) -> None:
    model = _model(hass)
    assert not model.finish_recording(), "nothing learned yet"
    _heat_up(model)
    model.finish_recording()
    await model.async_save()
    assert not model.finish_recording(), "already saved"

    restarted = _model(hass)
    await restarted.async_load()
    assert not restarted.finish_recording(), "just loaded"
    _heat_up(restarted)
    assert restarted.finish_recording(), "learned on top of the loaded model"


async def test_a_failed_save_is_retried_after_the_next_session(hass: HomeAssistant) -> None:
    model = _model(hass)
    _heat_up(model)
    model.finish_recording()
    with patch.object(model._store, "async_save", AsyncMock(side_effect=OSError)), \
            pytest.raises(OSError):
        await model.async_save()
    assert model.finish_recording()


async def test_a_session_end_writes_the_model_to_storage(
    hass: HomeAssistant, hass_storage
) -> None:
    coordinator, device, _ = await _cooldown(hass)
    _heat_up(coordinator.heating_model)
    samples = coordinator.heating_model.samples_total
    assert samples

    for _ in range(10):  # the cooldown confirms over several readings
        hass.states.async_set(REF, "40", force_update=True)
        await hass.async_block_till_done()
        coordinator._run_session_tracking(device)
        if not device._session_active:
            break
    assert not device._session_active
    await hass.async_block_till_done()

    entry_id = next(iter(hass.data[DOMAIN]))
    stored = hass_storage[f"{STORAGE_KEY_HEATING_MODEL}_{entry_id}"]["data"]
    assert stored["samples_total"] == samples
    assert stored["buckets"]


@pytest.mark.parametrize(
    ("start", "extra_min"),
    [(10, 0), (5, 10), (0, 20), (-10, 40), (-3.5, 27)],
)
def test_the_estimate_counts_a_cabin_below_10_degrees(start: float, extra_min: float) -> None:
    """The medium-mass seed is 0.5 °C/min down there."""
    with patch("custom_components.harvia_sauna.learning.Store", MagicMock()):
        model = _model(MagicMock())
    assert model.estimate_minutes(start, 80) == pytest.approx(
        model.estimate_minutes(10, 80) + extra_min
    )


def test_the_estimate_does_not_believe_a_broken_sensor() -> None:
    with patch("custom_components.harvia_sauna.learning.Store", MagicMock()):
        model = _model(MagicMock())
    assert model.estimate_minutes(-273, 80) == model.estimate_minutes(-30, 80)


def test_nothing_is_learned_below_10_degrees() -> None:
    with patch("custom_components.harvia_sauna.learning.Store", MagicMock()):
        model = _model(MagicMock())
    _heat_up(model, first=-5, last=10)
    assert not model.calibrated
