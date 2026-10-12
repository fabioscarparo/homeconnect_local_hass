"""Tests for binary sensor entity."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

from custom_components import homeconnect_ws
from custom_components.homeconnect_ws.const import DOMAIN
from custom_components.homeconnect_ws.entity_descriptions import HCBinarySensorEntityDescription
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import ATTR_FRIENDLY_NAME, STATE_OFF, STATE_ON, STATE_UNKNOWN

from . import setup_config_entry, update_entity
from .const import ENTITY_DESCRIPTIONS, MOCK_CONFIG_DATA

if TYPE_CHECKING:
    import pytest
    from home_disconnect import Appliance
    from homeassistant.core import HomeAssistant

PROBLEM_ENTITY_ID = "binary_sensor.fake_brand_homeappliance_problem"


async def test_setup(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """Test setting up entity."""
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)

    state = hass.states.get("binary_sensor.fake_brand_homeappliance_binarysensor")
    assert state
    # No value reported yet: unknown, not off (a sim's never-set
    # RemoteControlStartAllowed showed as "off" while Start still went through).
    assert state.state == STATE_UNKNOWN
    assert state.name == "Fake_brand HomeAppliance BinarySensor"
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Fake_brand HomeAppliance BinarySensor"

    state = hass.states.get("binary_sensor.fake_brand_homeappliance_binarysensor_enum")
    assert state
    assert state.state == STATE_UNKNOWN
    assert state.name == "Fake_brand HomeAppliance BinarySensor.Enum"
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Fake_brand HomeAppliance BinarySensor.Enum"


async def test_update(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """Test updating entity."""
    entity_id = "binary_sensor.fake_brand_homeappliance_binarysensor"
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)

    await update_entity(mock_appliance.entities["Test.BinarySensor"], {"value": True})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_ON

    await update_entity(mock_appliance.entities["Test.BinarySensor"], {"value": False})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF

    await update_entity(mock_appliance.entities["Test.BinarySensor"], {"value": None})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_UNKNOWN


async def test_connection_sensor_clean_disconnect_attribute(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """clean_disconnect says whether the connection ended with a normal close (code 1000)."""
    entity_id = "binary_sensor.fake_brand_homeappliance_connection"
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert hass.states.get(entity_id).attributes["clean_disconnect"] is False

    mock_appliance.session.connected = False
    mock_appliance.session.close_code = 1000
    entry.runtime_data.coordinator.async_set_updated_data(None)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state
    assert state.attributes["clean_disconnect"] is True

    mock_appliance.session.close_code = 1006
    entry.runtime_data.coordinator.async_set_updated_data(None)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.attributes["clean_disconnect"] is False


async def test_update_enum(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """Test updating entity with enum."""
    entity_id = "binary_sensor.fake_brand_homeappliance_binarysensor_enum"
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)

    await update_entity(mock_appliance.entities["Test.BinarySensor.Enum"], {"value": 0})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF

    await update_entity(mock_appliance.entities["Test.BinarySensor.Enum"], {"value": 1})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_ON


async def test_problem_sensor_follows_every_problem(
    hass: HomeAssistant, mock_appliance: Appliance, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Problem sensor is on while any problem is, including disabled ones."""
    problems = [
        HCBinarySensorEntityDescription(
            key="problem_1",
            name="Salt",
            entity="Test.Event1",
            device_class=BinarySensorDeviceClass.PROBLEM,
            value_on={"Present", "Confirmed"},
            value_off={"Off"},
        ),
        HCBinarySensorEntityDescription(
            key="problem_2",
            name="Pump",
            entity="Test.Event2",
            device_class=BinarySensorDeviceClass.PROBLEM,
            entity_registry_enabled_default=False,
            value_on={"Present", "Confirmed"},
            value_off={"Off"},
        ),
    ]
    descriptions = {**ENTITY_DESCRIPTIONS, "binary_sensor": problems}
    monkeypatch.setattr(homeconnect_ws, "get_available_entities", Mock(return_value=descriptions))
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)

    # Nothing reported yet.
    assert hass.states.get(PROBLEM_ENTITY_ID).state == STATE_UNKNOWN

    # One reported off, the other still unreported.
    await update_entity(mock_appliance.entities["Test.Event1"], {"value": 0})
    await hass.async_block_till_done()
    assert hass.states.get(PROBLEM_ENTITY_ID).state == STATE_OFF

    # The disabled problem sensor still counts.
    await update_entity(mock_appliance.entities["Test.Event2"], {"value": 1})
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.fake_brand_homeappliance_pump") is None
    assert hass.states.get(PROBLEM_ENTITY_ID).state == STATE_ON

    await update_entity(mock_appliance.entities["Test.Event2"], {"value": 0})
    await hass.async_block_till_done()
    assert hass.states.get(PROBLEM_ENTITY_ID).state == STATE_OFF


async def test_no_problem_sensor_without_problems(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """An appliance without problem sensors gets no Problem sensor."""
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)
    assert hass.states.get(PROBLEM_ENTITY_ID) is None
