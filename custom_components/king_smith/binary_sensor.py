"""WalkingPad binary-sensor support."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WalkingPadIntegrationData
from .const import DOMAIN
from .coordinator import WalkingPadCoordinator


CONNECTED_KEY = "walkingpad_connected"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the WalkingPad binary sensors."""

    entry_data: WalkingPadIntegrationData = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["coordinator"]

    async_add_entities([WalkingPadConnectedBinarySensor(coordinator)])


class WalkingPadConnectedBinarySensor(BinarySensorEntity):
    """Reflects whether the BLE link to the WalkingPad is currently up.

    Goes off in a few situations the user cares about:
      * Stay-connected was turned off (and the 5 s idle window has elapsed)
      * The treadmill dropped the link (weak signal, firmware quirk)
      * The phone app is currently holding the link (BLE is single-client)
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    entity_description = BinarySensorEntityDescription(
        key=CONNECTED_KEY,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        translation_key=CONNECTED_KEY,
    )

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the binary sensor."""
        self.coordinator = coordinator
        self._attr_unique_id = (
            f"{coordinator.walkingpad_device.mac}-{CONNECTED_KEY}"
        )
        self._attr_suggested_object_id = CONNECTED_KEY
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.walkingpad_device.mac)},
            name=coordinator.walkingpad_device.name,
            manufacturer="KingSmith",
            model=coordinator.walkingpad_device.name,
            sw_version=coordinator.walkingpad_device.firmware_version or None,
        )

    @property
    def is_on(self) -> bool:
        """Return True when the BLE link is currently established."""
        return self.coordinator.connected

    @property
    def available(self) -> bool:
        """The connectivity sensor is always available — it has a meaningful
        value (False) even when the link is down."""
        return True

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates so we re-render on state changes.

        The coordinator notifies its listeners on every status update *and*
        on the library's disconnect callback (`_async_handle_disconnect`),
        which is enough to track the BLE link state with negligible lag.
        """
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_state_change)
        )

    @callback
    def _handle_state_change(self) -> None:
        """Re-render in response to coordinator updates."""
        self.async_write_ha_state()
