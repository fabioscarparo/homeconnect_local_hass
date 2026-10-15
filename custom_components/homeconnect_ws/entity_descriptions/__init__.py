"""Description for all supported Entities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from homeassistant.const import UnitOfTemperature

from custom_components.homeconnect_ws.helpers import merge_dicts

from .common import COMMON_ENTITY_DESCRIPTIONS
from .consumer_products import CONSUMER_PRODUCTS_ENTITY_DESCRIPTIONS
from .cooking import COOKING_ENTITY_DESCRIPTIONS
from .descriptions_definitions import (
    EntityDescriptions,
    HCBinarySensorEntityDescription,
    HCButtonEntityDescription,
    HCEntityDescription,
    HCFanEntityDescription,
    HCLightEntityDescription,
    HCNumberEntityDescription,
    HCSelectEntityDescription,
    HCSensorEntityDescription,
    HCSwitchEntityDescription,
    HCUpdateEntityDescription,
    _EntityDescriptionsDefinitionsType,
    _EntityDescriptionsType,
    keep_known_events,
)
from .dishcare import DISHCARE_ENTITY_DESCRIPTIONS
from .laundry_care import LAUNDRY_ENTITY_DESCRIPTIONS
from .refrigeration import REFRIGERATION_ENTITY_DESCRIPTIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from home_disconnect import Appliance


_LOGGER = logging.getLogger(__name__)

FAHRENHEIT_KEY_SUFFIX = "_fahrenheit"

ALL_ENTITY_DESCRIPTIONS: _EntityDescriptionsDefinitionsType | None = None


def get_all_entity_description() -> _EntityDescriptionsDefinitionsType:
    global ALL_ENTITY_DESCRIPTIONS  # noqa: PLW0603
    if ALL_ENTITY_DESCRIPTIONS is None:
        ALL_ENTITY_DESCRIPTIONS = merge_dicts(
            COMMON_ENTITY_DESCRIPTIONS,
            CONSUMER_PRODUCTS_ENTITY_DESCRIPTIONS,
            COOKING_ENTITY_DESCRIPTIONS,
            DISHCARE_ENTITY_DESCRIPTIONS,
            LAUNDRY_ENTITY_DESCRIPTIONS,
            REFRIGERATION_ENTITY_DESCRIPTIONS,
        )
    return ALL_ENTITY_DESCRIPTIONS


def _resolve_description(
    description: HCEntityDescription
    | Callable[[Appliance], HCEntityDescription | EntityDescriptions | None],
    appliance: Appliance,
) -> HCEntityDescription | None:
    """
    Resolve a single per-type description entry to a concrete instance, if any.

    Note: HA's frozen_or_thawed EntityDescription machinery clones these
    classes into homeassistant.util.frozen_dataclass_compat at runtime, so
    isinstance() checks against HCEntityDescription (or any subclass) are
    unreliable here - callable() duck-typing is the only thing that actually
    distinguishes a description instance from a per-appliance generator
    function.
    """
    if not callable(description):
        return description
    dynamic_result = description(appliance)
    if not dynamic_result:
        return None
    return cast("HCEntityDescription", dynamic_result)


def _available_description(
    description_type: str, description: HCEntityDescription, appliance_entities: set[str]
) -> HCEntityDescription | None:
    """Return the description if this appliance can back it, else None."""
    if description_type == "event_sensor":
        return keep_known_events(cast("HCSensorEntityDescription", description), appliance_entities)
    subscribed: set[str] = set()
    if description.entity:
        subscribed.add(description.entity)
    if description.entities:
        subscribed.update(description.entities)
    return description if appliance_entities.issuperset(subscribed) else None


def get_available_entities(
    appliance: Appliance, temperature_unit: str = UnitOfTemperature.CELSIUS
) -> _EntityDescriptionsType:
    """
    Get all available Entity descriptions.

    temperature_unit is Home Assistant's unit system, which picks Celsius or
    Fahrenheit for a temperature the appliance reports in both.
    """
    available_entities: _EntityDescriptionsType = {
        "button": [],
        "active_program": [],
        "binary_sensor": [],
        "event_sensor": [],
        "number": [],
        "program": [],
        "select": [],
        "sensor": [],
        "start_button": [],
        "delay_start": [],
        "switch": [],
        "wifi": [],
        "ipv4": [],
        "ipv6": [],
        "light": [],
        "fan": [],
        "update": [],
    }
    appliance_entities = set(appliance.entities)
    for description_type, descriptions in get_all_entity_description().items():
        # dynamic descriptions: each callable builds a full set of new
        # entities (across types) for this specific appliance.
        if description_type == "dynamic":
            for descriptions_fn in descriptions:
                if not callable(descriptions_fn):
                    continue
                dynamic_result = descriptions_fn(appliance)
                if not isinstance(dynamic_result, dict):
                    continue
                dynamic_descriptions = cast("_EntityDescriptionsType", dynamic_result)
                for key, value in dynamic_descriptions.items():
                    available_entities[key].extend(value)
            continue
        for description in descriptions:
            resolved_description = _resolve_description(description, appliance)
            if resolved_description is None:
                continue
            available_description = _available_description(
                description_type, resolved_description, appliance_entities
            )
            if available_description is not None:
                available_entities[description_type].append(available_description)
    for available_type, available in available_entities.items():
        available_entities[available_type] = _one_temperature_unit(
            _first_per_key(available), temperature_unit
        )
    return available_entities


def _one_temperature_unit(
    descriptions: list[HCEntityDescription], temperature_unit: str
) -> list[HCEntityDescription]:
    """
    Keep one of a Celsius and a Fahrenheit description for the same temperature.

    Some appliances (e.g. Thermador fridges and ovens) report a setpoint in both
    units, as two entities. Keeps the one in Home Assistant's unit, so a setpoint
    steps in whole degrees of the unit the user sees; Home Assistant converts the
    shown value either way. A Fahrenheit description's key is the Celsius one's
    with FAHRENHEIT_KEY_SUFFIX.
    """
    keys = {description.key for description in descriptions}
    keep_fahrenheit = temperature_unit == UnitOfTemperature.FAHRENHEIT
    kept = []
    for description in descriptions:
        if description.key.endswith(FAHRENHEIT_KEY_SUFFIX):
            celsius_key = description.key.removesuffix(FAHRENHEIT_KEY_SUFFIX)
            if celsius_key in keys and not keep_fahrenheit:
                continue
        elif keep_fahrenheit and description.key + FAHRENHEIT_KEY_SUFFIX in keys:
            continue
        kept.append(description)
    return kept


def _first_per_key(descriptions: list[HCEntityDescription]) -> list[HCEntityDescription]:
    """
    Keep only the first description for each key.

    Some keys are shared by an old and a new name for the same thing (for example
    Refrigeration.FridgeFreezer.Setting.EcoMode and Refrigeration.Common.Setting.EcoMode),
    so existing entities keep their unique IDs whichever name the appliance uses. An
    appliance reporting both would otherwise get two entities with one unique ID.
    """
    kept: dict[str, HCEntityDescription] = {}
    for description in descriptions:
        if description.key in kept:
            _LOGGER.debug("Skipping a second entity for key %s", description.key)
            continue
        kept[description.key] = description
    return list(kept.values())


__all__ = [
    "EntityDescriptions",
    "HCBinarySensorEntityDescription",
    "HCButtonEntityDescription",
    "HCEntityDescription",
    "HCFanEntityDescription",
    "HCLightEntityDescription",
    "HCNumberEntityDescription",
    "HCSelectEntityDescription",
    "HCSensorEntityDescription",
    "HCSwitchEntityDescription",
    "HCUpdateEntityDescription",
    "_EntityDescriptionsType",
    "get_available_entities",
]
