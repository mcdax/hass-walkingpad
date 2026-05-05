"""The walkingpad integration."""

from __future__ import annotations

import logging
from typing import TypedDict

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import CONF_MAC, CONF_NAME, DOMAIN
from .coordinator import WalkingPadCoordinator
from .walkingpad import WalkingPad

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
]


class WalkingPadIntegrationData(TypedDict):
    """A type to represent the data stored by the integration for each entity."""

    device: WalkingPad
    coordinator: WalkingPadCoordinator


_LOGGER = logging.getLogger(__name__)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options and reload platforms."""
    await hass.config_entries.async_unload_platforms(
        entry, [Platform.SWITCH, Platform.NUMBER]
    )
    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform.SWITCH, Platform.NUMBER]
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up walkingpad from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    address = entry.data.get(CONF_MAC)

    ble_device = bluetooth.async_ble_device_from_address(
        hass, entry.data.get(CONF_MAC), connectable=True
    )
    if ble_device is None:
        # Check if any HA scanner on:
        count_scanners = bluetooth.async_scanner_count(hass, connectable=True)
        if count_scanners < 1:
            raise ConfigEntryNotReady(
                "No bluetooth scanner detected. \
                Enable the bluetooth integration or ensure an esphome device \
                is running as a bluetooth proxy"
            )
        raise ConfigEntryNotReady(f"Could not find Walkingpad with address {address}")

    name = entry.data.get(CONF_NAME) or DOMAIN
    _async_migrate_entity_ids(hass, entry)
    walkingpad_device = WalkingPad(name, ble_device)
    coordinator = WalkingPadCoordinator(hass, walkingpad_device)

    integration_data: WalkingPadIntegrationData = {
        "device": walkingpad_device,
        "coordinator": coordinator,
    }
    hass.data[DOMAIN][entry.entry_id] = integration_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


# One-time entity_id migration. Earlier versions of this integration registered
# entities with HA-auto-generated entity_ids derived from the localized
# friendly_name, leading to inconsistent / collision-prone IDs like
# `sensor.state`, `switch.stay_connected`, and `sensor.walkingpad_kalorienrate`
# (German). New installs use `_attr_suggested_object_id` to land on canonical
# English IDs from the start; this migration brings existing installs in line.
#
# Maps unique_id key → desired entity_id slug (no platform prefix).
_ENTITY_ID_MIGRATIONS: dict[str, str] = {
    "walkingpad_distance": "walkingpad_distance",
    "walkingpad_duration": "walkingpad_duration",
    "walkingpad_current_speed": "walkingpad_current_speed",
    "walkingpad_state": "walkingpad_state",
    "walkingpad_mode": "walkingpad_mode",
    "walkingpad_steps": "walkingpad_steps",
    "walkingpad_calories": "walkingpad_calories",
    "walkingpad_calories_per_hour": "walkingpad_calories_per_hour",
    "walkingpad_heart_rate": "walkingpad_heart_rate",
    "walkingpad_training_status": "walkingpad_training_status",
    "walkingpad_last_event": "walkingpad_last_event",
    "walkingpad_firmware_version": "walkingpad_firmware_version",
    "walkingpad_protocol": "walkingpad_protocol",
    "walkingpad_min_speed": "walkingpad_min_speed",
    "walkingpad_max_speed": "walkingpad_max_speed",
    "walkingpad_speed_increment": "walkingpad_speed_increment",
    "walkingpad_speed": "walkingpad_speed",
    "walkingpad_belt_switch": "walkingpad_belt",
    "walkingpad_stay_connected": "walkingpad_stay_connected",
    "walkingpad_connected": "walkingpad_connected",
}

# Translation_key migrations: collapse the manual/auto belt switch variants
# into a single `walkingpad_belt` so the friendly_name no longer carries a
# misleading mode suffix when the user changes preferred_mode.
_TRANSLATION_KEY_MIGRATIONS: dict[str, str] = {
    "walkingpad_belt_switch": "walkingpad_belt",
    "walkingpad_belt_switch_manual": "walkingpad_belt",
    "walkingpad_belt_switch_auto": "walkingpad_belt",
}


def _async_migrate_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Bring legacy entity_ids and translation_keys onto the new canonical scheme."""
    entity_registry = er.async_get(hass)
    mac = entry.data.get(CONF_MAC)
    if not mac:
        return

    entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    for ent in entries:
        # Each unique_id is `{MAC}-{key}`. Pull the key.
        if not ent.unique_id.startswith(f"{mac}-"):
            continue
        key = ent.unique_id[len(mac) + 1 :]

        desired_slug = _ENTITY_ID_MIGRATIONS.get(key)
        if desired_slug is None:
            continue
        desired_entity_id = f"{ent.domain}.{desired_slug}"

        new_translation_key = _TRANSLATION_KEY_MIGRATIONS.get(
            ent.translation_key or "", ent.translation_key
        )

        updates: dict[str, str] = {}
        if (
            ent.entity_id != desired_entity_id
            and entity_registry.async_get(desired_entity_id) is None
        ):
            _LOGGER.info(
                "Migrating entity_id %s -> %s", ent.entity_id, desired_entity_id
            )
            updates["new_entity_id"] = desired_entity_id
        if (
            new_translation_key is not None
            and ent.translation_key != new_translation_key
        ):
            _LOGGER.info(
                "Migrating translation_key on %s: %s -> %s",
                ent.entity_id,
                ent.translation_key,
                new_translation_key,
            )
            updates["translation_key"] = new_translation_key

        if updates:
            entity_registry.async_update_entity(ent.entity_id, **updates)
