"""Tests for the Problem sensor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

from custom_components import homeconnect_ws
from custom_components.homeconnect_ws.entity_descriptions import (
    HCBinarySensorEntityDescription,
    get_all_entity_description,
)
from custom_components.homeconnect_ws.entity_descriptions.cooking import _OVEN_COOKING_EVENTS
from custom_components.homeconnect_ws.sensor import problem_state
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.components.sensor import ATTR_OPTIONS
from homeassistant.const import STATE_UNKNOWN

from . import setup_config_entry, update_entity
from .const import ENTITY_DESCRIPTIONS, MOCK_CONFIG_DATA

if TYPE_CHECKING:
    import pytest
    from home_disconnect import Appliance
    from homeassistant.core import HomeAssistant

PROBLEM_ENTITY_ID = "sensor.fake_brand_homeappliance_problem"


def problem(
    key: str, entity: str, *, enabled: bool = True, in_problem_sensor: bool = True
) -> HCBinarySensorEntityDescription:
    """Build a problem binary sensor description."""
    return HCBinarySensorEntityDescription(
        key=key,
        entity=entity,
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=enabled,
        in_problem_sensor=in_problem_sensor,
        value_on={"Present", "Confirmed"},
        value_off={"Off"},
    )


async def test_problem_sensor_shows_the_active_problem(
    hass: HomeAssistant, mock_appliance: Appliance, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Problem sensor shows the active problem, including ones whose sensor is disabled."""
    problems = [
        problem("binary_sensor_salt", "Test.Event1"),
        problem("binary_sensor_pump_error", "Test.Event2", enabled=False),
    ]
    descriptions = {**ENTITY_DESCRIPTIONS, "binary_sensor": problems}
    monkeypatch.setattr(homeconnect_ws, "get_available_entities", Mock(return_value=descriptions))
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)

    # Nothing reported yet.
    state = hass.states.get(PROBLEM_ENTITY_ID)
    assert state.state == STATE_UNKNOWN
    assert state.attributes[ATTR_OPTIONS] == ["ok", "salt", "pump_error"]

    # One reported off, the other still unreported.
    await update_entity(mock_appliance.entities["Test.Event1"], {"value": 0})
    await hass.async_block_till_done()
    assert hass.states.get(PROBLEM_ENTITY_ID).state == "ok"

    # The disabled problem sensor still counts.
    await update_entity(mock_appliance.entities["Test.Event2"], {"value": 1})
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.fake_brand_homeappliance_pump_error") is None
    assert hass.states.get(PROBLEM_ENTITY_ID).state == "pump_error"

    await update_entity(mock_appliance.entities["Test.Event2"], {"value": 0})
    await hass.async_block_till_done()
    assert hass.states.get(PROBLEM_ENTITY_ID).state == "ok"


async def test_problem_sensor_leaves_out_alarms_and_reminders(
    hass: HomeAssistant, mock_appliance: Appliance, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alarms and reminders only have their own binary sensors."""
    problems = [
        problem("binary_sensor_pump_error", "Test.Event1", enabled=False),
        problem("binary_sensor_drum_clean_reminder", "Test.Event2", in_problem_sensor=False),
    ]
    descriptions = {**ENTITY_DESCRIPTIONS, "binary_sensor": problems}
    monkeypatch.setattr(homeconnect_ws, "get_available_entities", Mock(return_value=descriptions))
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)
    assert hass.states.get(PROBLEM_ENTITY_ID).attributes[ATTR_OPTIONS] == ["ok", "pump_error"]

    await update_entity(mock_appliance.entities["Test.Event1"], {"value": 0})
    await update_entity(mock_appliance.entities["Test.Event2"], {"value": 1})
    await hass.async_block_till_done()
    assert hass.states.get(PROBLEM_ENTITY_ID).state == "ok"
    assert (
        hass.states.get("binary_sensor.fake_brand_homeappliance_drum_clean_reminder").state == "on"
    )


async def test_no_problem_sensor_with_only_alarms_and_reminders(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An appliance whose problems are all alarms or reminders gets no Problem sensor."""
    problems = [problem("binary_sensor_door_alarm_fridge", "Test.Event1", in_problem_sensor=False)]
    descriptions = {**ENTITY_DESCRIPTIONS, "binary_sensor": problems}
    monkeypatch.setattr(homeconnect_ws, "get_available_entities", Mock(return_value=descriptions))
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)
    assert hass.states.get(PROBLEM_ENTITY_ID) is None


async def test_no_problem_sensor_without_problems(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """An appliance without problem sensors gets no Problem sensor."""
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)
    assert hass.states.get(PROBLEM_ENTITY_ID) is None


def test_every_problem_has_a_state_name() -> None:
    """Every problem the Problem sensor counts has a translated state, and nothing else does."""
    states = {
        problem_state(description)
        for description in get_all_entity_description()["binary_sensor"]
        if not callable(description)
        and description.device_class == BinarySensorDeviceClass.PROBLEM
        and description.in_problem_sensor
    }
    states.update(
        key.removeprefix("binary_sensor_")
        for _, key, device_class in _OVEN_COOKING_EVENTS
        if device_class == BinarySensorDeviceClass.PROBLEM
    )
    translations = json.loads(
        (Path(homeconnect_ws.__file__).parent / "translations" / "en.json").read_text()
    )["entity"]["sensor"]["sensor_problem"]["state"]
    assert set(translations) == {"ok", *states}
