"""WalkingPad button support — currently a single Stop Session button."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import (
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WalkingPadIntegrationData
from .const import CONF_MAC, CONF_REMOTE_CONTROL_ENABLED, DOMAIN
from .coordinator import WalkingPadCoordinator


STOP_SESSION_KEY = "walkingpad_stop_session"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the WalkingPad button(s).

    The Stop button is a hard reset — it ends the session and zeros the
    counters (time, distance, calories, steps). Gated by
    remote_control_enabled like the belt switch, since users who haven't
    opted into remote control shouldn't see a "stop" they can't pair with
    a "start".
    """
    entry_data: WalkingPadIntegrationData = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["coordinator"]

    if entry.options.get(CONF_REMOTE_CONTROL_ENABLED, False):
        async_add_entities([WalkingPadStopSessionButton(coordinator)])
        return

    # Remote control was disabled — clean up the entity if it existed
    # from a previous configuration.
    entity_registry = er.async_get(hass)
    mac_address = entry.data.get(CONF_MAC)
    unique_id = f"{mac_address}-{STOP_SESSION_KEY}"
    entity_id = entity_registry.async_get_entity_id("button", DOMAIN, unique_id)
    if entity_id:
        entity_registry.async_remove(entity_id)


class WalkingPadStopSessionButton(ButtonEntity):
    """Hard-stop the WalkingPad and reset the session counters.

    Different from toggling the belt switch off — that path now sends
    PAUSE (counters carry over). This button is the explicit "end session
    and start over" action, matching what an end-of-workout button would
    do on the phone app.

    Only available while the BLE link is up. Pressing it while
    disconnected wouldn't do anything useful (the lib would have to
    reconnect first, which can take seconds and might race with the
    user's next action).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    entity_description = ButtonEntityDescription(
        key=STOP_SESSION_KEY,
        icon="mdi:stop",
        translation_key=STOP_SESSION_KEY,
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the Stop button."""
        self.coordinator = coordinator
        self._attr_unique_id = (
            f"{coordinator.walkingpad_device.mac}-{STOP_SESSION_KEY}"
        )
        self._attr_suggested_object_id = STOP_SESSION_KEY
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.walkingpad_device.mac)},
            name=coordinator.walkingpad_device.name,
            manufacturer="KingSmith",
            model=coordinator.walkingpad_device.name,
            sw_version=coordinator.walkingpad_device.firmware_version or None,
        )

    @property
    def available(self) -> bool:
        """The button is only enabled when the BLE link is up."""
        return self.coordinator.connected

    async def async_press(self) -> None:
        """Send a hard stop — full session end, counters reset."""
        await self.coordinator.walkingpad_device.stop_belt()

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates so `available` re-renders on
        connect / disconnect events."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_state_change)
        )

    def _handle_state_change(self) -> None:
        """Re-render — picks up changes to `coordinator.connected`."""
        self.async_write_ha_state()
