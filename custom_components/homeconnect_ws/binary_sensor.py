"""Binary Sensor entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import HomeConnectCoordinator
from .entity import HCEntity
from .entity_descriptions.descriptions_definitions import HCBinarySensorEntityDescription
from .helpers import create_entities

if TYPE_CHECKING:
    from home_disconnect import Appliance
    from home_disconnect import Entity as HcEntity
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import HCConfigEntry, HCData

PARALLEL_UPDATES = 0

CONNECTION_SENSOR_DESCRIPTIONS = HCBinarySensorEntityDescription(
    key="connection",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)

PROBLEM_SENSOR_KEY = "binary_sensor_problem"


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    config_entry: HCConfigEntry,
    async_add_entites: AddEntitiesCallback,
) -> None:
    """Set up binary_sensor platform."""
    entities: set[HCEntity | HCConnectionSensor] = set(
        create_entities({"binary_sensor": HCBinarySensor}, config_entry.runtime_data)
    )
    entities.add(HCConnectionSensor(CONNECTION_SENSOR_DESCRIPTIONS, config_entry.runtime_data))
    problem_descriptions = [
        cast("HCBinarySensorEntityDescription", description)
        for description in config_entry.runtime_data.available_entity_descriptions.get(
            "binary_sensor", []
        )
        if description.device_class == BinarySensorDeviceClass.PROBLEM
    ]
    if problem_descriptions:
        entities.add(HCProblemSensor(problem_descriptions, config_entry.runtime_data))
    async_add_entites(entities)


def _is_on(description: HCBinarySensorEntityDescription, entity: HcEntity | None) -> bool | None:
    """Whether the entity's value means on, or None if it's unknown."""
    if entity is None:
        return None
    if description.value_on:
        if entity.value in description.value_on:
            return True
        if description.value_off and entity.value in description.value_off:
            return False
        return None
    if entity.value is None:
        # Not reported by the appliance yet - "unknown", not "off".
        return None
    return bool(entity.value)


class HCBinarySensor(HCEntity, BinarySensorEntity):
    """Binary Sensor Entity."""

    entity_description: HCBinarySensorEntityDescription

    @property
    def is_on(self) -> bool | None:
        return _is_on(self.entity_description, self._entity)


class HCProblemSensor(HCEntity, BinarySensorEntity):
    """
    On while any of the appliance's problem sensors is on.

    Most per-problem sensors are disabled by default, so this is the one to watch; enable a
    per-problem sensor to see or automate on that problem alone.
    """

    entity_description: HCBinarySensorEntityDescription

    def __init__(
        self, problem_descriptions: list[HCBinarySensorEntityDescription], runtime_data: HCData
    ) -> None:
        super().__init__(
            HCBinarySensorEntityDescription(
                key=PROBLEM_SENSOR_KEY,
                entities=[
                    description.entity for description in problem_descriptions if description.entity
                ],
                device_class=BinarySensorDeviceClass.PROBLEM,
            ),
            runtime_data,
        )
        self._problems = [
            (description, runtime_data.appliance.entities[description.entity])
            for description in problem_descriptions
            if description.entity
        ]

    @property
    def is_on(self) -> bool | None:
        states = [_is_on(description, entity) for description, entity in self._problems]
        if any(states):
            return True
        if all(state is None for state in states):
            # None of them reported yet.
            return None
        # A problem the appliance hasn't reported isn't active.
        return False


class HCConnectionSensor(CoordinatorEntity[HomeConnectCoordinator], BinarySensorEntity):
    """Connection sensor Entity."""

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_available = True
    entity_description: HCBinarySensorEntityDescription

    def __init__(
        self, entity_description: HCBinarySensorEntityDescription, runtime_data: HCData
    ) -> None:
        super().__init__(runtime_data.coordinator)
        self._appliance: Appliance = runtime_data.appliance
        self.entity_description = entity_description
        self._attr_unique_id = f"{runtime_data.appliance.info['deviceID']}-{entity_description.key}"
        self._attr_device_info: DeviceInfo = runtime_data.device_info
        self._attr_translation_key = entity_description.key

    @property
    def is_on(self) -> bool:
        return self._appliance.connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Whether the last connection ended with a normal close (code 1000), as washers and
        # dryers do when switched off; False while connected.
        return {"clean_disconnect": self._appliance.closed_cleanly is True}
