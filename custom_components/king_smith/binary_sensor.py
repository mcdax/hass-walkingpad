"""WalkingPad binary-sensor support."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WalkingPadIntegrationData
from .const import DOMAIN
from .coordinator import WalkingPadCoordinator
from .entity import WalkingPadPushEntity


CONNECTED_KEY = "walkingpad_connected"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the WalkingPad binary sensors."""

    entry_data: WalkingPadIntegrationData = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["coordinator"]

    async_add_entities([WalkingPadConnectedBinarySensor(coordinator)])


class WalkingPadConnectedBinarySensor(WalkingPadPushEntity, BinarySensorEntity):
    """Reflects whether the BLE link to the WalkingPad is currently up.

    Goes off in a few situations the user cares about:
      * Stay-connected was turned off (the coordinator disconnects right
        away; after commands the link drops 5 s after the last one)
      * The treadmill dropped the link (weak signal, firmware quirk)
      * The phone app is currently holding the link (BLE is single-client)

    Re-renders on every coordinator notification, which includes the
    library's disconnect callback (`_async_handle_disconnect`).
    """

    entity_description = BinarySensorEntityDescription(
        key=CONNECTED_KEY,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        translation_key=CONNECTED_KEY,
    )

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, CONNECTED_KEY)

    @property
    def is_on(self) -> bool:
        """Return True when the BLE link is currently established."""
        return self.coordinator.connected

    @property
    def available(self) -> bool:
        """Return True: the sensor has a meaningful value (off) even when the link is down."""
        return True
