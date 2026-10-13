"""Tests for washer options and remote control entities from a Bosch WGH24400IT washer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.homeconnect_ws.entity_descriptions import get_available_entities
from home_disconnect import Access

if TYPE_CHECKING:
    from . import MockApplianceFactory


def _option(uid: int, name: str, **extra: Any) -> dict[str, Any]:
    return {"uid": uid, "name": name, "available": True, "access": Access.READ_WRITE, **extra}


def _enumeration(*values: str) -> dict[str, Any]:
    return {
        "contentType": "enumeration",
        "protocolType": "Integer",
        "enumeration": {str(index): value for index, value in enumerate(values)},
    }


WASHER_DESCRIPTION = {
    "option": [
        _option(
            24602,
            "LaundryCare.Washer.Option.MiniLoad",
            contentType="boolean",
            protocolType="Boolean",
        ),
        _option(
            24599,
            "LaundryCare.Washer.Option.PreTreatment",
            **_enumeration("Off", "Prewash", "HandSoak"),
        ),
        _option(
            24607,
            "LaundryCare.Washer.Option.SoakTemperature",
            **_enumeration("Cold", "GC20", "GC30", "GC40"),
        ),
        _option(
            24608,
            "LaundryCare.Washer.Option.SoakDuration",
            contentType="timeSpan",
            protocolType="Integer",
            min="900",
            max="14400",
            stepSize="900",
        ),
        _option(
            24597,
            "LaundryCare.Washer.Option.HygienicSteamIntensity",
            **_enumeration("Middle", "High"),
        ),
    ],
    "command": [
        {
            "uid": 555,
            "name": "BSH.Common.Command.DeactivateRemoteControlStart",
            "available": True,
            "access": Access.WRITE_ONLY,
            "contentType": "boolean",
            "protocolType": "Boolean",
        },
    ],
    "event": [
        {
            "uid": 46,
            "name": "BSH.Common.Event.ConfirmPermanentRemoteStart",
            **_enumeration("Off", "Present", "Confirmed"),
        },
    ],
}


def _by_key(available: dict[str, list[Any]]) -> dict[str, Any]:
    return {
        description.key: description
        for descriptions in available.values()
        for description in descriptions
    }


async def test_washer_options(mock_homeconnect_appliance: MockApplianceFactory) -> None:
    """Mini load, pre-treatment and soak options become entities."""
    appliance = await mock_homeconnect_appliance(description=WASHER_DESCRIPTION)
    descriptions = _by_key(get_available_entities(appliance))

    assert descriptions["switch_laundry_mini_load"].entity == "LaundryCare.Washer.Option.MiniLoad"
    assert (
        descriptions["select_laundry_pretreatment"].entity
        == "LaundryCare.Washer.Option.PreTreatment"
    )
    assert (
        descriptions["select_laundry_soak_temperature"].entity
        == "LaundryCare.Washer.Option.SoakTemperature"
    )
    assert (
        descriptions["number_laundry_soak_duration"].entity
        == "LaundryCare.Washer.Option.SoakDuration"
    )


async def test_washer_hygienic_steam_intensity(
    mock_homeconnect_appliance: MockApplianceFactory,
) -> None:
    """A washer with the Washer name of the steam intensity gets the same select."""
    appliance = await mock_homeconnect_appliance(description=WASHER_DESCRIPTION)
    descriptions = _by_key(get_available_entities(appliance))

    select = descriptions["select_laundry_hygienic_steam_intensity"]
    assert select.entity == "LaundryCare.Washer.Option.HygienicSteamIntensity"


async def test_remote_control_entities(mock_homeconnect_appliance: MockApplianceFactory) -> None:
    """The remote start button and the permanent remote start event are offered."""
    appliance = await mock_homeconnect_appliance(description=WASHER_DESCRIPTION)
    descriptions = _by_key(get_available_entities(appliance))

    assert (
        descriptions["button_deactivate_remote_control_start"].entity
        == "BSH.Common.Command.DeactivateRemoteControlStart"
    )
    # This washer has no DeactivateRemoteControl command
    assert "button_deactivate_remote_control" not in descriptions
    event = descriptions["binary_sensor_confirm_permanent_remote_start"]
    assert event.value_on == {"Present", "Confirmed"}
