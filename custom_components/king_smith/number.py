"""Walkingpad number support."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import WalkingPadIntegrationData
from .const import (
    CONF_MAC,
    CONF_PREFERRED_MODE,
    CONF_REMOTE_CONTROL_ENABLED,
    DEFAULT_PREFERRED_MODE,
    DOMAIN,
    BeltState,
    ProtocolType,
    WalkingPadMode,
)
from .coordinator import WalkingPadCoordinator

NUMBER_KEY = "walkingpad_speed"
VIBRATION_KEY = "walkingpad_vibration"
INCLINE_KEY = "walkingpad_incline"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the WalkingPad number."""

    remote_control_enabled = entry.options.get(CONF_REMOTE_CONTROL_ENABLED, False)
    preferred_mode = entry.options.get(CONF_PREFERRED_MODE, DEFAULT_PREFERRED_MODE)
    manual_mode = WalkingPadMode.MANUAL.name.lower()

    if not (remote_control_enabled and preferred_mode == manual_mode):
        entity_registry = er.async_get(hass)
        mac_address = entry.data.get(CONF_MAC)
        for key in (NUMBER_KEY, VIBRATION_KEY, INCLINE_KEY):
            unique_id = f"{mac_address}-{key}"
            entity_id = entity_registry.async_get_entity_id("number", DOMAIN, unique_id)
            if entity_id:
                entity_registry.async_remove(entity_id)
        return

    entry_data: WalkingPadIntegrationData = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["coordinator"]

    entities: list[NumberEntity] = [WalkingPadSpeedNumberEntity(coordinator)]
    # Incline and vibration are Sperax P3 Max (WLT6200) features only.
    if coordinator.walkingpad_device.protocol == ProtocolType.SPERAX:
        entities.append(WalkingPadInclineNumberEntity(coordinator))
        entities.append(WalkingPadVibrationNumberEntity(coordinator))

    async_add_entities(entities)


class WalkingPadSpeedNumberEntity(
    CoordinatorEntity[WalkingPadCoordinator], NumberEntity
):
    """Represent the WalkingPad speed number."""

    _attr_mode = NumberMode.AUTO
    _attr_native_unit_of_measurement = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_has_entity_name = True
    _attr_translation_key = "walkingpad_speed"

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the speed number."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.walkingpad_device.mac}-{NUMBER_KEY}"
        self._attr_suggested_object_id = NUMBER_KEY
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.walkingpad_device.mac)},
            name=coordinator.walkingpad_device.name,
            manufacturer="KingSmith",
            model=coordinator.walkingpad_device.name,
            sw_version=coordinator.walkingpad_device.firmware_version or None,
        )

    @property
    def native_min_value(self) -> float:
        """Return min speed from device capabilities (updates after connection)."""
        return self.coordinator.walkingpad_device.min_speed

    @property
    def native_max_value(self) -> float:
        """Return max speed from device capabilities (updates after connection)."""
        return self.coordinator.walkingpad_device.max_speed

    @property
    def native_step(self) -> float:
        """Return speed increment from device capabilities (updates after connection)."""
        return self.coordinator.walkingpad_device.speed_increment

    @property
    def native_value(self) -> float | None:
        """Return the current speed, or None when disconnected.

        Returning None marks the displayed value as unknown but keeps
        the entity available so the user can still send a target speed
        — see `available` below.
        """
        if not self.coordinator.connected:
            return None
        return self.coordinator.data.get("speed", 0.0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the speed.

        For FTMS devices, setting a target speed also starts the belt
        (the library handles the cold-start sequence), so we accept
        speed changes whether or not the belt is currently running and
        whether or not we are currently connected — the underlying
        WalkingPad wrapper will connect-and-issue as needed.

        For legacy WiLink devices, the firmware only accepts speed
        changes while the belt is moving, so we keep the existing
        belt-state guard for them.
        """
        from .const import ProtocolType

        device = self.coordinator.walkingpad_device
        belt_state = self.coordinator.data.get("belt_state")

        if device.protocol == ProtocolType.WILINK and belt_state not in [
            BeltState.ACTIVE,
            BeltState.STARTING,
        ]:
            return

        await device.set_speed(value)

    @property
    def available(self) -> bool:
        """The slider stays available even when the BLE link is down.

        On FTMS devices `set_native_value` triggers a connect-and-set
        sequence, so the user can drag the slider to start a walk
        directly without first toggling Stay-connected on. On WiLink
        devices the slider is hidden (entity removed) when remote
        control isn't enabled, so this only ever matters for FTMS.
        """
        return True


class WalkingPadVibrationNumberEntity(
    CoordinatorEntity[WalkingPadCoordinator], NumberEntity
):
    """Vibration level control for Sperax P3 Max (WLT6200) devices.

    Level 0 turns vibration off; 1-4 select the intensity. On the P3 Max the
    belt and the vibration motor are mutually exclusive — selecting a level
    stops the belt.
    """

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 4
    _attr_native_step = 1
    _attr_has_entity_name = True
    _attr_translation_key = VIBRATION_KEY

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the vibration number."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.walkingpad_device.mac}-{VIBRATION_KEY}"
        self._attr_suggested_object_id = VIBRATION_KEY
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.walkingpad_device.mac)},
            name=coordinator.walkingpad_device.name,
            manufacturer="Sperax",
            model=coordinator.walkingpad_device.name,
            sw_version=coordinator.walkingpad_device.firmware_version or None,
        )

    @property
    def native_value(self) -> float | None:
        """Return the current vibration level, or None when disconnected."""
        if not self.coordinator.connected:
            return None
        return self.coordinator.data.get("vibration_level", 0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the vibration level (0 = off, 1-4)."""
        await self.coordinator.walkingpad_device.set_vibration(int(value))


class WalkingPadInclineNumberEntity(
    CoordinatorEntity[WalkingPadCoordinator], NumberEntity
):
    """Incline level control for Sperax P3 Max (WLT6200) devices.

    Range 0 (flat) to 10 (max). The device has no decline. Incline rides
    inside the run command, so the library only applies it while the belt is
    moving and otherwise caches it for the next start.
    """

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_has_entity_name = True
    _attr_translation_key = INCLINE_KEY

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the incline number."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.walkingpad_device.mac}-{INCLINE_KEY}"
        self._attr_suggested_object_id = INCLINE_KEY
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.walkingpad_device.mac)},
            name=coordinator.walkingpad_device.name,
            manufacturer="Sperax",
            model=coordinator.walkingpad_device.name,
            sw_version=coordinator.walkingpad_device.firmware_version or None,
        )

    @property
    def native_value(self) -> float | None:
        """Return the current incline level, or None when disconnected."""
        if not self.coordinator.connected:
            return None
        return self.coordinator.data.get("incline", 0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the incline level (0-10)."""
        await self.coordinator.walkingpad_device.set_incline(int(value))
