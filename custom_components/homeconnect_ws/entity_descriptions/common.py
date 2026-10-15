"""Description for BSH.Common Entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from home_disconnect.entities import Access, Execution
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.components.switch.const import SwitchDeviceClass
from homeassistant.components.update import UpdateDeviceClass
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfEnergy,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.util import dt as dt_util

from custom_components.homeconnect_ws.helpers import delay_start_entity

from .descriptions_definitions import (
    EntityDescriptions,
    HCBinarySensorEntityDescription,
    HCButtonEntityDescription,
    HCNumberEntityDescription,
    HCSelectEntityDescription,
    HCSensorEntityDescription,
    HCSwitchEntityDescription,
    HCUpdateEntityDescription,
    _EntityDescriptionsDefinitionsType,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from home_disconnect import Appliance
    from homeassistant.helpers.typing import StateType


POWER_SWITCH_VALUE_MAPINGS = (
    ("On", "MainsOff"),
    ("Standby", "MainsOff"),
    ("On", "Off"),
    ("On", "Standby"),
    ("Standby", "Off"),
)

# BSH appliances name their powered-down PowerState value either "Off" or
# "MainsOff" depending on the model (see POWER_SWITCH_VALUE_MAPINGS above,
# which already accounts for both). Checked in this order so "Off" wins if
# an appliance's enum somehow had both.
POWER_OFF_STATE_NAMES = ("Off", "MainsOff")


def generate_start_button(appliance: Appliance) -> HCButtonEntityDescription | None:
    """Get Start Button description."""
    # SELECT_ONLY needs this button just as much as SELECT_AND_START: selecting
    # a program (writing SelectedProgram) only stages it and its options on
    # these appliances, it doesn't start anything - a separate write to
    # ActiveProgram is what actually starts it, and Program.start() already
    # posts there unconditionally regardless of execution type. Confirmed
    # live on fork issue #21 via the official cloud integration's own debug
    # log: "PUT .../programs/active {'key': '<program>'}" is the literal
    # start action, distinct from the earlier "PUT .../programs/selected"
    # that only configured options.
    #
    # Permissive by default (exclude only confirmed START_ONLY), not
    # restrictive: this generator runs once, synchronously, before the
    # appliance connection is even attempted (get_available_entities() is
    # called before coordinator.async_config_entry_first_refresh() - see
    # __init__.py) - for laundry appliances, whose setup is deliberately
    # non-blocking, that can mean *no* live data has arrived yet at all.
    # program.execution then reads whatever the static profile export
    # defaults to, which is frequently the placeholder "none" (confirmed on
    # fork issue #21's own washer - every program in a real profile export
    # showed execution="none", not the appliance's real value). Requiring a
    # positive SELECT_AND_START/SELECT_ONLY match meant the button silently
    # never got created for the rest of the session whenever this ran before
    # the real value arrived. HCStartButton.available is a live property and
    # correctly reflects the true execution type once data does arrive, so
    # the only actual cost of over-including here is a harmless extra button
    # on a genuinely START_ONLY appliance - far cheaper than a required
    # button silently missing for the whole session.
    programs = list(
        filter(
            lambda program: program.execution != Execution.START_ONLY,
            appliance.programs.values(),
        )
    )
    if len(programs) > 0:
        return HCButtonEntityDescription(
            key="button_start_program",
            entity="BSH.Common.Root.ActiveProgram",
            # HCStartButton.available reads appliance.selected_program, which
            # resolves through SelectedProgram, not ActiveProgram - without
            # this, the button only re-renders when ActiveProgram itself
            # changes, so live power-on/off toggling (which updates
            # SelectedProgram) can leave the displayed availability a step
            # stale until something else happens to touch ActiveProgram.
            # Confirmed live on fork issue #54.
            entities=["BSH.Common.Root.SelectedProgram"],
        )
    return None


def generate_delay_start(appliance: Appliance) -> HCNumberEntityDescription | None:
    """
    Get the "Delay start" number description.

    Offered whenever the appliance has StartInRelative or FinishInRelative.
    Its value is the delay itself and lives in HA, not on the appliance: some
    appliances accept the delay only as an option of the program start (#146),
    so the Start button applies it then.
    """
    entity = delay_start_entity(appliance)
    if entity is None:
        return None
    return HCNumberEntityDescription(
        key="number_delay_start",
        translation_key="number_delay_start",
        entity=entity.name,
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=0,
        mode=NumberMode.BOX,
    )


def generate_power_switch(appliance: Appliance) -> EntityDescriptions:
    """
    Get the Power entity description: one switch or select.

    A switch when the appliance switches between two states (on and off, or on
    and standby), and a select for anything else. There's no read-only sensor:
    while the appliance locks its power state, the switch or select shows it
    read-only, like the Home Connect app does. The profile's access can't
    decide this, since a profile marking it "read" doesn't mean it stays that
    way (fork issue #7's washer was turned off through it).
    """
    entity_descriptions = EntityDescriptions()
    entity = appliance.entities.get("BSH.Common.Setting.PowerState")
    if entity is None:
        return entity_descriptions

    entity_min = getattr(entity, "min", None)
    entity_max = getattr(entity, "max", None)
    if entity_min is not None and entity_max is not None:
        # has min/max
        settable_states = set()
        for key, value in (entity.enum or {}).items():
            if int(key) >= entity_min and int(key) <= entity_max:
                settable_states.add(value)
    else:
        settable_states = set((entity.enum or {}).values())

    for mapping in POWER_SWITCH_VALUE_MAPINGS:
        if settable_states == set(mapping):
            entity_descriptions["switch"] = [
                HCSwitchEntityDescription(
                    key="switch_power_state",
                    entity="BSH.Common.Setting.PowerState",
                    device_class=SwitchDeviceClass.SWITCH,
                    value_mapping=mapping,
                    # MainsOff, Standby (when "On" is the on side) and Off all
                    # mean not on, even the ones the switch never sets.
                    on_values=tuple(sorted({mapping[0], "On"})),
                    force_off_when_expected_offline=True,
                )
            ]
            return entity_descriptions

    entity_descriptions["select"] = [
        HCSelectEntityDescription(
            key="select_power_state",
            entity="BSH.Common.Setting.PowerState",
            options=[value.lower() for value in settable_states],
            has_state_translation=True,
            force_option_when_expected_offline=_power_off_option(settable_states),
        )
    ]
    return entity_descriptions


def _power_off_option(states: set[str]) -> str | None:
    """
    Return the option for powered off, if the appliance has one.

    "off" is only a valid option if this appliance's PowerState actually has
    Off/MainsOff - forcing to a name that isn't one of its real options makes
    SelectEntity.state fall back to None (shown as "Unknown"), confirmed live
    on fork issue #7.
    """
    return next((name.lower() for name in POWER_OFF_STATE_NAMES if name in states), None)


def generate_door_state(appliance: Appliance) -> EntityDescriptions:
    """
    Get the Door entity description: one binary sensor or sensor.

    A binary sensor when the door is only open or closed, and an enum sensor
    when it has more states (e.g. locked or ajar), which a binary sensor can't
    tell apart.
    """
    entity_descriptions = EntityDescriptions()
    entity = appliance.entities.get("BSH.Common.Status.DoorState")
    if entity is None:
        return entity_descriptions
    if len(entity.enum or {}) > 2:
        entity_descriptions["sensor"] = [
            HCSensorEntityDescription(
                key="sensor_door_state",
                entity="BSH.Common.Status.DoorState",
                device_class=SensorDeviceClass.ENUM,
                has_state_translation=True,
            )
        ]
    else:
        entity_descriptions["binary_sensor"] = [
            HCBinarySensorEntityDescription(
                key="binary_sensor_door_state",
                entity="BSH.Common.Status.DoorState",
                device_class=BinarySensorDeviceClass.DOOR,
                value_on={"Open", "Ajar"},
                value_off={"Closed", "Locked"},
            )
        ]
    return entity_descriptions


def generate_program(appliance: Appliance) -> EntityDescriptions:
    """Get Door program select and sensor description."""
    favorites = {
        f"BSH.Common.Program.Favorite.{favorite}": favorite
        for favorite in appliance.group_ids("BSH.Common.Program.Favorite")
    }

    programs = {}

    for program in appliance.programs:
        if (favorite := favorites.get(program)) is not None:
            favorite_name_entity = appliance.settings.get(
                f"BSH.Common.Setting.Favorite.{favorite}.Name"
            )
            if favorite_name_entity and favorite_name_entity.value:
                program_name = favorite_name_entity.value
            else:
                program_name = f"favorite_{favorite}"
        else:
            program_name = program.lower().replace(".", "_")

        programs[program] = program_name

    # sort programs
    programs_keys = list(programs.keys())
    programs_keys.sort()
    sorted_programs = {i: programs[i] for i in programs_keys}

    descriptions = EntityDescriptions()
    if programs:
        descriptions["active_program"] = [
            HCSensorEntityDescription(
                key="sensor_active_program",
                entity="BSH.Common.Root.ActiveProgram",
                device_class=SensorDeviceClass.ENUM,
                has_state_translation=False,
                mapping=sorted_programs,
                clear_on_expected_offline=True,
            )
        ]
        descriptions["program"] = [
            HCSelectEntityDescription(
                key="select_program",
                entity="BSH.Common.Root.SelectedProgram",
                has_state_translation=False,
                mapping=sorted_programs,
            )
        ]

    return descriptions


PROGRAM_SESSION_SUMMARY = "BSH.Common.Status.ProgramSessionSummary.Latest"
ENERGY_CONSUMED_LAST = "BSH.Common.Status.Program.Last.Energy.Consumed"
WATER_CONSUMED_LAST = "BSH.Common.Status.Program.Last.Water.Consumed"


def session_summary_detail(appliance: Appliance, uid: int) -> object:
    """Get a value from the details of the latest Program Session Summary."""
    summary = appliance.entities.get(PROGRAM_SESSION_SUMMARY)
    if summary is None or not isinstance(summary.value, dict):
        return None
    for run in reversed(summary.value.get("sequence", [])):
        for detail in run.get("details", []):
            if detail.get("uid") == uid:
                return detail.get("value")
    return None


def last_program_value(appliance: Appliance, name: str) -> float | None:
    """
    Get the value of a Program.Last status.

    Falls back to the latest Program Session Summary: a Bosch WGH24400IT
    washer leaves the Program.Last statuses empty after reconnecting, while
    the summary still lists them.
    """
    status = appliance.entities[name]
    value = status.value
    if value is None:
        value = session_summary_detail(appliance, status.uid)
    return value if isinstance(value, int | float) else None


def generate_last_program_consumption(appliance: Appliance) -> EntityDescriptions:
    """
    Get the energy and water consumption of the last program.

    When the appliance has a Program Session Summary, the sensors follow it
    instead of the Program.Last status, so they still get a value while the
    status is empty, and they listen to the status too.
    """
    descriptions = EntityDescriptions(sensor=[])
    summary = PROGRAM_SESSION_SUMMARY if PROGRAM_SESSION_SUMMARY in appliance.entities else None

    def value_fn(name: str, scale: int = 1) -> Callable[[StateType], StateType]:
        def read(_: StateType) -> StateType:
            value = last_program_value(appliance, name)
            return None if value is None else value / scale

        return read

    if ENERGY_CONSUMED_LAST in appliance.entities:
        descriptions["sensor"].append(
            HCSensorEntityDescription(
                key="sensor_energy_consumed_last",
                entity=summary or ENERGY_CONSUMED_LAST,
                entities=[ENERGY_CONSUMED_LAST] if summary else None,
                device_class=SensorDeviceClass.ENERGY,
                native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
                suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                value_fn=value_fn(ENERGY_CONSUMED_LAST),
            )
        )
    if WATER_CONSUMED_LAST in appliance.entities:
        descriptions["sensor"].append(
            HCSensorEntityDescription(
                key="sensor_water_consumed_last",
                entity=summary or WATER_CONSUMED_LAST,
                entities=[WATER_CONSUMED_LAST] if summary else None,
                device_class=SensorDeviceClass.WATER,
                native_unit_of_measurement=UnitOfVolume.LITERS,
                # Reported in mL, which the water device class doesn't take
                value_fn=value_fn(WATER_CONSUMED_LAST, scale=1000),
            )
        )
    return descriptions


def generate_wifi(appliance: Appliance) -> HCSensorEntityDescription:  # noqa: ARG001
    """
    Get WiFi sensor description.

    Always uses the polling HCWiFI sensor (queries /ni/info directly) rather than
    the appliance's BSH.Common.Status.WiFiSignalStrength status entity: that status
    is only ever populated once at connect and is never pushed again afterward, so
    it goes permanently stale until the next reconnect.
    """
    return HCSensorEntityDescription(
        key="sensor_wifi_signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    )


def generate_ipv4(appliance: Appliance) -> HCSensorEntityDescription:  # noqa: ARG001
    """Get IPv4 address sensor description. Polled the same way as generate_wifi."""
    return HCSensorEntityDescription(
        key="sensor_ipv4_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    )


def generate_ipv6(appliance: Appliance) -> HCSensorEntityDescription:  # noqa: ARG001
    """Get IPv6 address sensor description. Polled the same way as generate_wifi."""
    return HCSensorEntityDescription(
        key="sensor_ipv6_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    )


def generate_temperature_unit(appliance: Appliance) -> HCSelectEntityDescription | None:
    """
    Get Temperature unit description.

    Left out when the appliance's unit is fixed (Access.READ_STATIC, e.g. some
    ovens), where the select could never be changed. Temperature entities still
    follow the unit either way (see HCEntity).
    """
    entity = appliance.entities.get("BSH.Common.Setting.TemperatureUnit")
    if entity and entity.access is not Access.READ_STATIC and len(entity.enum or {}) >= 2:
        return HCSelectEntityDescription(
            key="select_temperature_unit",
            entity="BSH.Common.Setting.TemperatureUnit",
            device_class=SensorDeviceClass.ENUM,
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=False,
            has_state_translation=True,
        )
    return None


def generate_software_download_update(
    appliance: Appliance,
) -> HCUpdateEntityDescription | None:
    """Get Software Download update description, if a separate download stage exists."""
    if (
        "BSH.Common.Event.SoftwareDownloadAvailable" in appliance.entities
        and "BSH.Common.Command.AllowSoftwareDownload" in appliance.entities
    ):
        return HCUpdateEntityDescription(
            key="update_software_download",
            entity="BSH.Common.Event.SoftwareDownloadAvailable",
            command_entity="BSH.Common.Command.AllowSoftwareDownload",
            device_class=UpdateDeviceClass.FIRMWARE,
            entity_category=EntityCategory.CONFIG,
        )
    return None


def generate_software_update(appliance: Appliance) -> HCUpdateEntityDescription | None:
    """Get Software Update (install) description."""
    if (
        "BSH.Common.Event.SoftwareUpdateAvailable" in appliance.entities
        and "BSH.Common.Command.AllowSoftwareUpdateLocalWiFi" in appliance.entities
    ):
        return HCUpdateEntityDescription(
            key="update_software_update",
            entity="BSH.Common.Event.SoftwareUpdateAvailable",
            command_entity="BSH.Common.Command.AllowSoftwareUpdateLocalWiFi",
            device_class=UpdateDeviceClass.FIRMWARE,
            entity_category=EntityCategory.CONFIG,
        )
    return None


COMMON_ENTITY_DESCRIPTIONS: _EntityDescriptionsDefinitionsType = {
    "button": [
        HCButtonEntityDescription(
            key="button_abort_program",
            entity="BSH.Common.Command.AbortProgram",
        ),
        HCButtonEntityDescription(
            key="button_pause_program",
            entity="BSH.Common.Command.PauseProgram",
        ),
        HCButtonEntityDescription(
            key="button_resume_program",
            entity="BSH.Common.Command.ResumeProgram",
        ),
        HCButtonEntityDescription(
            key="button_mains_power_off",
            entity="BSH.Common.Command.MainsPowerOff",
        ),
        HCButtonEntityDescription(
            key="button_deactivate_remote_control",
            entity="BSH.Common.Command.DeactivateRemoteControl",
            entity_category=EntityCategory.CONFIG,
            # One press locks Home Assistant out until it's turned on at the appliance
            entity_registry_enabled_default=False,
        ),
        HCButtonEntityDescription(
            key="button_deactivate_remote_control_start",
            entity="BSH.Common.Command.DeactivateRemoteControlStart",
            entity_category=EntityCategory.CONFIG,
            # One press locks Home Assistant out until it's turned on at the appliance
            entity_registry_enabled_default=False,
        ),
        HCButtonEntityDescription(
            key="button_sync_time",
            entity="BSH.Common.Setting.ApplianceDateTime",
            entity_category=EntityCategory.CONFIG,
            # Only useful with "Synchronize time with server" off, which is
            # itself disabled by default, so follow it.
            entity_registry_enabled_default=False,
            press_fn=lambda appliance: appliance.set_datetime(dt_util.now()),
        ),
    ],
    "binary_sensor": [
        HCBinarySensorEntityDescription(
            key="binary_sensor_aqua_stop",
            device_class=BinarySensorDeviceClass.PROBLEM,
            entity="BSH.Common.Event.AquaStopOccured",
            entity_registry_enabled_default=False,
            value_on={"Present"},
            value_off={"Off", "Confirmed"},
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_low_water_pressure",
            device_class=BinarySensorDeviceClass.PROBLEM,
            entity="BSH.Common.Event.LowWaterPressure",
            entity_registry_enabled_default=False,
            value_on={"Present"},
            value_off={"Off", "Confirmed"},
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        HCBinarySensorEntityDescription(
            key="binary_remote_start_allowed",
            entity="BSH.Common.Status.RemoteControlStartAllowed",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_confirm_permanent_remote_start",
            entity="BSH.Common.Event.ConfirmPermanentRemoteStart",
            entity_category=EntityCategory.DIAGNOSTIC,
            value_on={"Present", "Confirmed"},
            value_off={"Off"},
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_program_aborted",
            entity="BSH.Common.Event.ProgramAborted",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=BinarySensorDeviceClass.PROBLEM,
            entity_registry_enabled_default=False,
            value_on={"Present", "Confirmed"},
            value_off={"Off"},
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_program_finished",
            entity="BSH.Common.Event.ProgramFinished",
            entity_category=EntityCategory.DIAGNOSTIC,
            value_on={"Present", "Confirmed"},
            value_off={"Off"},
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_interior_illumination",
            entity="BSH.Common.Status.InteriorIlluminationActive",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_alarm_clock_elapsed",
            entity="BSH.Common.Event.AlarmClockElapsed",
            value_on={"Present", "Confirmed"},
            value_off={"Off"},
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_favorite_001",
            entity="BSH.Common.Event.Favorite.001.ExternalTrigger",
            value_on={"Present", "Confirmed"},
            value_off={"Off"},
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_favorite_002",
            entity="BSH.Common.Event.Favorite.002.ExternalTrigger",
            value_on={"Present", "Confirmed"},
            value_off={"Off"},
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_local_control_active",
            entity="BSH.Common.Status.LocalControlActive",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_remote_control_active",
            entity="BSH.Common.Status.RemoteControlActive",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
        ),
        HCBinarySensorEntityDescription(
            key="binary_sensor_backend_connected",
            entity="BSH.Common.Status.BackendConnected",
            device_class=BinarySensorDeviceClass.CONNECTIVITY,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
        ),
    ],
    "select": [
        HCSelectEntityDescription(
            key="select_remote_control_level",
            entity="BSH.Common.Setting.RemoteControlLevel",
            entity_category=EntityCategory.CONFIG,
            has_state_translation=True,
        ),
        # cleanup: duplicate select_remote_control_level entry removed
        HCSelectEntityDescription(
            key="select_time_format",
            entity="BSH.Common.Setting.TimeFormat",
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=False,
            has_state_translation=True,
        ),
        generate_temperature_unit,
    ],
    "sensor": [
        HCSensorEntityDescription(
            key="sensor_remaining_program_time",
            entity="BSH.Common.Option.RemainingProgramTime",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            suggested_unit_of_measurement=UnitOfTime.HOURS,
            extra_attributes=[
                {
                    "name": "Is Estimated",
                    "entity": "BSH.Common.Option.RemainingProgramTimeIsEstimated",
                }
            ],
            clear_on_expected_offline=True,
        ),
        HCSensorEntityDescription(
            key="sensor_elapsed_program_time",
            entity="BSH.Common.Option.ElapsedProgramTime",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            suggested_unit_of_measurement=UnitOfTime.HOURS,
            clear_on_expected_offline=True,
            available_while_program_active=True,
        ),
        HCSensorEntityDescription(
            key="sensor_program_progress",
            entity="BSH.Common.Option.ProgramProgress",
            native_unit_of_measurement=PERCENTAGE,
            clear_on_expected_offline=True,
            unavailable_when_no_active_program=True,
        ),
        HCSensorEntityDescription(
            key="sensor_water_forecast",
            entity="BSH.Common.Option.WaterForecast",
            native_unit_of_measurement=PERCENTAGE,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        HCSensorEntityDescription(
            key="sensor_energy_forecast",
            entity="BSH.Common.Option.EnergyForecast",
            native_unit_of_measurement=PERCENTAGE,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        HCSensorEntityDescription(
            key="sensor_operation_state",
            entity="BSH.Common.Status.OperationState",
            device_class=SensorDeviceClass.ENUM,
            has_state_translation=True,
        ),
        HCSensorEntityDescription(
            key="sensor_start_in",
            entity="BSH.Common.Option.StartInRelative",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            suggested_unit_of_measurement=UnitOfTime.HOURS,
        ),
        HCSensorEntityDescription(
            key="sensor_finish_in",
            entity="BSH.Common.Option.FinishInRelative",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            suggested_unit_of_measurement=UnitOfTime.HOURS,
        ),
        HCSensorEntityDescription(
            key="sensor_count_started",
            entity="BSH.Common.Status.Program.All.Count.Started",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            state_class=SensorStateClass.TOTAL_INCREASING,
            extra_attributes=[
                {
                    "name": "Last Start",
                    "entity": "BSH.Common.Status.ProgramSessionSummary.Latest",
                    "value_fn": lambda entity: (
                        entity.value["start"] if entity.value is not None else None
                    ),
                },
                {
                    "name": "Last End",
                    "entity": "BSH.Common.Status.ProgramSessionSummary.Latest",
                    "value_fn": lambda entity: (
                        entity.value["end"] if entity.value is not None else None
                    ),
                },
            ],
        ),
        HCSensorEntityDescription(
            key="sensor_count_completed",
            entity="BSH.Common.Status.Program.All.Count.Completed",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        HCSensorEntityDescription(
            key="sensor_energy_consumed",
            entity="BSH.Common.Status.Program.All.Energy.Consumed",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
            suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ),
        HCSensorEntityDescription(
            key="sensor_water_consumed",
            entity="BSH.Common.Status.Program.All.Water.Consumed",
            device_class=SensorDeviceClass.WATER,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=UnitOfVolume.LITERS,
            # Reported in mL, which the water device class doesn't take
            value_fn=lambda value: value / 1000 if isinstance(value, int | float) else None,
        ),
        HCSensorEntityDescription(
            key="sensor_end_trigger",
            entity="BSH.Common.Status.ProgramRunDetail.EndTrigger",
            device_class=SensorDeviceClass.ENUM,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            has_state_translation=True,
        ),
        HCSensorEntityDescription(
            key="sensor_flex_start",
            entity="BSH.Common.Status.FlexStart",
            device_class=SensorDeviceClass.ENUM,
            has_state_translation=True,
        ),
        HCSensorEntityDescription(
            key="sensor_estimated_remaining_program_time",
            entity="BSH.Common.Option.EstimatedTotalProgramTime",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            suggested_unit_of_measurement=UnitOfTime.HOURS,
            clear_on_expected_offline=True,
        ),
    ],
    "start_button": [generate_start_button],
    "delay_start": [generate_delay_start],
    "switch": [
        HCSwitchEntityDescription(
            key="switch_child_lock",
            entity="BSH.Common.Setting.ChildLock",
            device_class=SwitchDeviceClass.SWITCH,
        ),
        HCSwitchEntityDescription(
            key="switch_allow_backend_connection",
            entity="BSH.Common.Setting.AllowBackendConnection",
            device_class=SwitchDeviceClass.SWITCH,
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=False,
        ),
        HCSwitchEntityDescription(
            key="switch_allow_consumer_insights",
            entity="BSH.Common.Setting.AllowConsumerInsights",
            device_class=SwitchDeviceClass.SWITCH,
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=False,
        ),
        HCSwitchEntityDescription(
            key="switch_synchronize_with_time_server",
            entity="BSH.Common.Setting.SynchronizeWithTimeServer",
            device_class=SwitchDeviceClass.SWITCH,
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=False,
        ),
    ],
    "number": [
        HCNumberEntityDescription(
            key="number_duration",
            entity="BSH.Common.Option.Duration",
            device_class=NumberDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            mode=NumberMode.AUTO,
            clear_on_expected_offline=True,
        ),
        HCNumberEntityDescription(
            key="number_setting_alarm_clock",
            translation_key="number_setting_alarm_clock",
            entity="BSH.Common.Setting.AlarmClock",
            device_class=NumberDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            native_max_value=86400,  # 24 hours, matching the Home Connect App's limit
            mode=NumberMode.BOX,
        ),
    ],
    "wifi": [generate_wifi],
    "ipv4": [generate_ipv4],
    "ipv6": [generate_ipv6],
    "update": [generate_software_download_update, generate_software_update],
    "dynamic": [
        generate_power_switch,
        generate_door_state,
        generate_program,
        generate_last_program_consumption,
    ],
}
