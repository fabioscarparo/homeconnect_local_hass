"""Tests for the "Delay start" number and how the Start button passes it on."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from custom_components.homeconnect_ws.entity_descriptions.common import generate_delay_start
from custom_components.homeconnect_ws.helpers import (
    FINISH_IN_RELATIVE,
    START_IN_RELATIVE,
    DelayStart,
    delay_start_entity,
)
from home_disconnect.messages import Action, Message
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.number import ATTR_MAX, ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, ATTR_UNIT_OF_MEASUREMENT, UnitOfTime
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

from . import setup_config_entry, update_entity
from .const import MOCK_CONFIG_DATA

if TYPE_CHECKING:
    from home_disconnect import Appliance
    from homeassistant.core import HomeAssistant

NUMBER_ENTITY_ID = "number.fake_brand_homeappliance_delay_start"
START_ENTITY_ID = "button.fake_brand_homeappliance_activeprogram"


def _option(name: str, uid: int, value: int | None = None, max_value: int = 86400) -> MagicMock:
    option = MagicMock(uid=uid, value=value, max=max_value)
    option.name = name
    return option


def _appliance(*options: MagicMock) -> MagicMock:
    appliance = MagicMock()
    appliance.entities = {option.name: option for option in options}
    return appliance


def test_delay_start_reset() -> None:
    """reset() goes back to no delay and notifies."""
    delay_start = DelayStart()
    assert delay_start.seconds == 0
    delay_start.seconds = 3 * 3600

    calls = []
    remove = delay_start.add_listener(lambda: calls.append(True))
    delay_start.reset()
    assert delay_start.seconds == 0
    assert calls == [True]

    remove()
    delay_start.reset()
    assert calls == [True]


def test_delay_start_entity_prefers_start_in() -> None:
    """StartInRelative takes the delay as-is, so it wins over FinishInRelative."""
    start_in = _option(START_IN_RELATIVE, 550)
    finish_in = _option(FINISH_IN_RELATIVE, 551)
    assert delay_start_entity(_appliance(start_in, finish_in)) is start_in
    assert delay_start_entity(_appliance(finish_in)) is finish_in
    assert delay_start_entity(_appliance()) is None


def test_generate_delay_start() -> None:
    """The number is only offered on appliances with StartIn or FinishIn."""
    assert generate_delay_start(_appliance()) is None
    description = generate_delay_start(_appliance(_option(FINISH_IN_RELATIVE, 551)))
    assert description is not None
    assert description.entity == FINISH_IN_RELATIVE
    assert description.native_unit_of_measurement == UnitOfTime.MINUTES


async def _set_delay(hass: HomeAssistant, minutes: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: NUMBER_ENTITY_ID, ATTR_VALUE: minutes},
        blocking=True,
    )


async def test_number_defaults_to_no_delay_and_stores_it(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """The number starts at 0 minutes and keeps the delay in HA only."""
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)

    state = hass.states.get(NUMBER_ENTITY_ID)
    assert state
    assert float(state.state) == 0
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfTime.MINUTES
    # FinishInRelative's maximum, 86400 s.
    assert state.attributes[ATTR_MAX] == 1440

    await _set_delay(hass, 90)
    assert float(hass.states.get(NUMBER_ENTITY_ID).state) == 90
    # Nothing is written to the appliance until Start is pressed.
    mock_appliance.session.request.assert_not_awaited()


async def test_number_max_without_appliance_max(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """Without a maximum from the appliance, the delay goes up to 24 hours."""
    mock_appliance.entities["BSH.Common.Option.FinishInRelative"].max = None
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)
    assert hass.states.get(NUMBER_ENTITY_ID).attributes[ATTR_MAX] == 1440


async def test_number_restores_last_delay(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """A delay set before a restart is still there afterwards."""
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(NUMBER_ENTITY_ID, "180"),
                {
                    "native_value": 180,
                    "native_min_value": 0,
                    "native_max_value": 1440,
                    "native_step": 1,
                    "native_unit_of_measurement": UnitOfTime.MINUTES,
                },
            ),
        ),
    )
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)
    assert float(hass.states.get(NUMBER_ENTITY_ID).state) == 180
    entry = hass.config_entries.async_entries("homeconnect_ws")[0]
    assert entry.runtime_data.delay_start.seconds == 180 * 60


async def test_start_applies_delay_and_resets_it(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """
    Start sends FinishInRelative = program duration + delay, then resets the number.

    The washer reports FinishInRelative as the program's duration once a
    program is selected (5100 s here); 120 minutes later makes it 12300 s.
    """
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)
    await update_entity(mock_appliance.entities["Test.SelectedProgram"], {"value": 500})
    await update_entity(
        mock_appliance.entities["BSH.Common.Option.FinishInRelative"], {"value": 5100}
    )
    await hass.async_block_till_done()

    await _set_delay(hass, 120)
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: START_ENTITY_ID}, blocking=True
    )

    mock_appliance.session.request.assert_awaited_once_with(
        Message(
            resource="/ro/activeProgram",
            action=Action.POST,
            data=[{"program": 500, "options": [{"uid": 405, "value": 12300}]}],
        )
    )
    assert float(hass.states.get(NUMBER_ENTITY_ID).state) == 0


async def test_start_refuses_too_long_delay_and_keeps_it(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """A delay past the maximum is refused without starting, and stays set."""
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)
    await update_entity(mock_appliance.entities["Test.SelectedProgram"], {"value": 500})
    await update_entity(
        mock_appliance.entities["BSH.Common.Option.FinishInRelative"], {"value": 5100}
    )
    await hass.async_block_till_done()

    await _set_delay(hass, 1440)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: START_ENTITY_ID}, blocking=True
        )

    assert err.value.translation_key == "delay_start_too_long"
    assert err.value.translation_placeholders == {"max_hours": "24"}
    mock_appliance.session.request.assert_not_awaited()
    assert float(hass.states.get(NUMBER_ENTITY_ID).state) == 1440


async def test_start_refuses_delay_without_duration(
    hass: HomeAssistant,
    mock_appliance: Appliance,
    patch_entity_description: None,
) -> None:
    """A FinishInRelative delay needs the program's duration; without it nothing starts."""
    assert await setup_config_entry(hass, MOCK_CONFIG_DATA)
    await update_entity(mock_appliance.entities["Test.SelectedProgram"], {"value": 500})
    await hass.async_block_till_done()

    await _set_delay(hass, 60)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: START_ENTITY_ID}, blocking=True
        )

    assert err.value.translation_key == "delay_start_duration_unknown"
    mock_appliance.session.request.assert_not_awaited()
