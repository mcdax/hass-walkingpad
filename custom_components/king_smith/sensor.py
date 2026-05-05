"""WalkingPad sensor support."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfSpeed, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import WalkingPadIntegrationData
from .const import (
    DOMAIN,
    FTMS_FM_EVENT_NAMES,
    FTMS_TRAINING_STATUS_NAMES,
    BeltState,
    ProtocolType,
    WalkingPadMode,
    WalkingPadStatus,
)
from .coordinator import WalkingPadCoordinator


@dataclass(kw_only=True)
class WalkingPadSensorEntityDescription(SensorEntityDescription):
    """Describes a WalkingPad sensor entity.

    `value_fn` receives the coordinator so it can read either dynamic
    status (from `coord.data`) or static device-info (from
    `coord.walkingpad_device`).

    `static` flags sensors whose value comes from device capabilities
    rather than runtime status — these stay available even while the
    BLE link is down, since their value doesn't depend on live data.
    """

    value_fn: Callable[[WalkingPadCoordinator], StateType]
    static: bool = False


# Sensors available for all protocol types.
COMMON_SENSORS: tuple[WalkingPadSensorEntityDescription, ...] = (
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.DISTANCE,
        icon="mdi:walk",
        key="walkingpad_distance",
        name=None,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        translation_key="walkingpad_distance",
        value_fn=lambda coord: coord.data.get("session_distance", 0.0) / 1000,
    ),
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:timer",
        key="walkingpad_duration",
        name=None,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        translation_key="walkingpad_duration",
        value_fn=lambda coord: coord.data.get("session_running_time", 0),
    ),
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.SPEED,
        icon="mdi:speedometer",
        key="walkingpad_current_speed",
        name=None,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        translation_key="walkingpad_current_speed",
        value_fn=lambda coord: coord.data.get("speed", 0.0),
    ),
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:state-machine",
        key="walkingpad_state",
        name=None,
        options=[e.name.lower() for e in BeltState],
        translation_key="walkingpad_state",
        value_fn=lambda coord: coord.data.get(
            "belt_state", BeltState.UNKNOWN
        ).name.lower(),
    ),
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:cog",
        key="walkingpad_mode",
        name=None,
        options=[e.name.lower() for e in WalkingPadMode],
        translation_key="walkingpad_mode",
        value_fn=lambda coord: coord.data.get("mode", WalkingPadMode.MANUAL).name.lower(),
    ),
    WalkingPadSensorEntityDescription(
        icon="mdi:shoe-print",
        key="walkingpad_steps",
        name=None,
        native_unit_of_measurement="steps",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        translation_key="walkingpad_steps",
        value_fn=lambda coord: coord.data.get("session_steps", 0),
    ),
    # --- Static device-info sensors below.  `static=True` keeps them
    # available even while the BLE link is down. ---
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:bluetooth",
        key="walkingpad_protocol",
        name=None,
        options=["ftms", "wilink", "unknown"],
        static=True,
        translation_key="walkingpad_protocol",
        value_fn=lambda coord: coord.walkingpad_device.protocol.value,
    ),
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.SPEED,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:speedometer-slow",
        key="walkingpad_min_speed",
        name=None,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        static=True,
        suggested_display_precision=1,
        translation_key="walkingpad_min_speed",
        value_fn=lambda coord: coord.walkingpad_device.min_speed,
    ),
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.SPEED,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:speedometer",
        key="walkingpad_max_speed",
        name=None,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        static=True,
        suggested_display_precision=1,
        translation_key="walkingpad_max_speed",
        value_fn=lambda coord: coord.walkingpad_device.max_speed,
    ),
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.SPEED,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:plus-minus-variant",
        key="walkingpad_speed_increment",
        name=None,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        static=True,
        suggested_display_precision=2,
        translation_key="walkingpad_speed_increment",
        value_fn=lambda coord: coord.walkingpad_device.speed_increment,
    ),
)

# Sensors only available for FTMS devices.
FTMS_SENSORS: tuple[WalkingPadSensorEntityDescription, ...] = (
    WalkingPadSensorEntityDescription(
        icon="mdi:fire",
        key="walkingpad_calories",
        name=None,
        native_unit_of_measurement="kcal",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        translation_key="walkingpad_calories",
        value_fn=lambda coord: coord.data.get("session_calories", 0),
    ),
    WalkingPadSensorEntityDescription(
        icon="mdi:fire-circle",
        key="walkingpad_calories_per_hour",
        name=None,
        native_unit_of_measurement="kcal/h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        translation_key="walkingpad_calories_per_hour",
        value_fn=lambda coord: coord.data.get("session_calories_per_hour", 0),
    ),
    WalkingPadSensorEntityDescription(
        icon="mdi:heart-pulse",
        key="walkingpad_heart_rate",
        name=None,
        native_unit_of_measurement="bpm",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        translation_key="walkingpad_heart_rate",
        # Treadmill reports 0 when no HR strap is paired — mark unavailable
        # rather than report a misleading "0 bpm".
        value_fn=lambda coord: coord.data.get("heart_rate", 0) or None,
    ),
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:run",
        key="walkingpad_training_status",
        name=None,
        options=list(FTMS_TRAINING_STATUS_NAMES.values()),
        translation_key="walkingpad_training_status",
        value_fn=lambda coord: FTMS_TRAINING_STATUS_NAMES.get(
            coord.data.get("training_status", 0), "other"
        ),
    ),
    WalkingPadSensorEntityDescription(
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:bell-ring",
        key="walkingpad_last_event",
        name=None,
        options=list(FTMS_FM_EVENT_NAMES.values()),
        translation_key="walkingpad_last_event",
        value_fn=lambda coord: FTMS_FM_EVENT_NAMES.get(
            coord.data.get("last_fm_event", 0), "none"
        ),
    ),
    # Static device-info: firmware version is FTMS-only because it's
    # read from Software Revision String (0x2A28) during connect.
    WalkingPadSensorEntityDescription(
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
        key="walkingpad_firmware_version",
        name=None,
        static=True,
        translation_key="walkingpad_firmware_version",
        value_fn=lambda coord: coord.walkingpad_device.firmware_version or None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the WalkingPad sensors."""

    entry_data: WalkingPadIntegrationData = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["coordinator"]
    device = entry_data["device"]

    # Build sensor list based on protocol type.
    # Steps are in COMMON_SENSORS (available for both WiLink and FTMS).
    sensors: list[WalkingPadSensorEntityDescription] = list(COMMON_SENSORS)

    if device.protocol == ProtocolType.FTMS:
        sensors.extend(FTMS_SENSORS)

    async_add_entities(
        WalkingPadSensor(coordinator, description) for description in sensors
    )


class WalkingPadSensor(
    CoordinatorEntity[WalkingPadCoordinator],
    SensorEntity,
):
    """Represent a WalkingPad sensor."""

    entity_description: WalkingPadSensorEntityDescription

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WalkingPadCoordinator,
        entity_description: WalkingPadSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.walkingpad_device.mac}-{self.entity_description.key}"
        )
        # Pin the entity_id to a stable English slug derived from the
        # description key, so HA doesn't auto-generate it from the
        # localized friendly_name (which led to inconsistent IDs like
        # `sensor.walkingpad_kalorienrate` in the past).
        self._attr_suggested_object_id = self.entity_description.key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.walkingpad_device.mac)},
            name=coordinator.walkingpad_device.name,
            manufacturer="KingSmith",
            model=coordinator.walkingpad_device.name,
            sw_version=coordinator.walkingpad_device.firmware_version or None,
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Static device-info sensors (firmware version, protocol, speed
        range) stay available even when the BLE link is down — their
        value doesn't depend on a live connection.
        """
        if self.entity_description.static:
            return True
        return self.coordinator.connected
