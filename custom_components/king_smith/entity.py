"""Shared entity helpers for the WalkingPad integration."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_MAC, DOMAIN
from .coordinator import WalkingPadCoordinator
from .walkingpad import WalkingPad


def walkingpad_unique_id(device: WalkingPad, key: str) -> str:
    """Return the unique_id of the entity ``key`` on ``device``."""
    return f"{device.mac}-{key}"


def walkingpad_device_info(
    device: WalkingPad, manufacturer: str = "KingSmith"
) -> DeviceInfo:
    """Return a DeviceInfo snapshot for ``device``.

    The firmware version is only known after connecting; the coordinator
    pushes it into the device registry later (`_async_push_firmware_once`).
    """
    return DeviceInfo(
        identifiers={(DOMAIN, device.mac)},
        name=device.name,
        manufacturer=manufacturer,
        model=device.name,
        sw_version=device.firmware_version or None,
    )


@callback
def async_remove_stale_entities(
    hass: HomeAssistant, entry: ConfigEntry, platform: str, keys: Iterable[str]
) -> None:
    """Remove registry entries left over from a previous configuration."""
    entity_registry = er.async_get(hass)
    mac_address = entry.data.get(CONF_MAC)
    for key in keys:
        unique_id = f"{mac_address}-{key}"
        entity_id = entity_registry.async_get_entity_id(platform, DOMAIN, unique_id)
        if entity_id:
            entity_registry.async_remove(entity_id)


class WalkingPadPushEntity:
    """Mixin for entities that re-render on every coordinator notification.

    For entities that aren't CoordinatorEntity (their availability doesn't
    follow the coordinator's last update): sets up unique_id, entity_id
    slug and device info from ``key``, and subscribes to the coordinator.
    Put it before the HA platform entity class in the bases.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: WalkingPadCoordinator, key: str) -> None:
        """Initialize the entity."""
        self.coordinator = coordinator
        self._attr_unique_id = walkingpad_unique_id(coordinator.walkingpad_device, key)
        self._attr_suggested_object_id = key
        self._attr_device_info = walkingpad_device_info(coordinator.walkingpad_device)

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates so state re-renders promptly."""
        await super().async_added_to_hass()  # type: ignore[misc]
        self.async_on_remove(  # type: ignore[attr-defined]
            self.coordinator.async_add_listener(self._handle_state_change)
        )

    @callback
    def _handle_state_change(self) -> None:
        """Re-render in response to coordinator updates."""
        self.async_write_ha_state()  # type: ignore[attr-defined]
