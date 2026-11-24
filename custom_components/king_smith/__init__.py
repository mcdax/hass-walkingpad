"""The walkingpad integration."""

from __future__ import annotations

import logging
from typing import TypedDict

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_BLE_NAME,
    CONF_IDLE_DISCONNECT_MINUTES,
    CONF_MAC,
    CONF_NAME,
    DEFAULT_IDLE_DISCONNECT_MINUTES,
    DOMAIN,
)
from .coordinator import WalkingPadCoordinator
from .walkingpad import WalkingPad

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
]


class WalkingPadIntegrationData(TypedDict):
    """A type to represent the data stored by the integration per config entry."""

    device: WalkingPad
    coordinator: WalkingPadCoordinator
    # Options the control platforms were last set up with.
    options: dict


_LOGGER = logging.getLogger(__name__)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options and reload platforms.

    The platforms reloaded here are the ones whose entity set depends on
    options (specifically `remote_control_enabled`). The button platform
    is in this list so the Stop button appears / disappears alongside
    the belt switch when the user toggles remote-control.
    """
    integration_data: WalkingPadIntegrationData | None = hass.data[DOMAIN].get(
        entry.entry_id
    )
    if integration_data is None:
        return
    # The listener also fires for entry.data updates (the learned BLE
    # name); reloading the controls then would interrupt a running walk.
    options = dict(entry.options)
    if options == integration_data["options"]:
        return
    reloadable_platforms = [Platform.SWITCH, Platform.NUMBER, Platform.BUTTON]
    if not await hass.config_entries.async_unload_platforms(
        entry, reloadable_platforms
    ):
        _LOGGER.warning("Could not unload the control platforms; not reloading them")
        return
    await hass.config_entries.async_forward_entry_setups(entry, reloadable_platforms)
    # Only now: after a failed unload the next options update retries.
    integration_data["options"] = options
    _apply_idle_disconnect(integration_data["coordinator"], entry)


@callback
def _apply_idle_disconnect(
    coordinator: WalkingPadCoordinator, entry: ConfigEntry
) -> None:
    """Hand the idle-disconnect option to the coordinator."""
    coordinator.set_idle_disconnect_minutes(
        entry.options.get(CONF_IDLE_DISCONNECT_MINUTES, DEFAULT_IDLE_DISCONNECT_MINUTES)
    )


def _advertised_name(hass: HomeAssistant, address: str) -> str | None:
    """Return the name the treadmill advertises, if HA has seen it.

    HA merges the scan response into its service info, so this can know
    the name while the BLEDevice handed out still has none.
    """
    info = bluetooth.async_last_service_info(hass, address, connectable=True)
    if info is None or not info.name or info.name == info.address:
        return None
    return info.name


@callback
def _async_store_ble_name(
    hass: HomeAssistant, entry: ConfigEntry, ble_name: str | None
) -> None:
    """Remember the advertised name for protocol detection.

    After a restart the proxy often hands over a nameless BLEDevice, and the
    entry name is free text that need not match a protocol prefix.
    """
    if ble_name and ble_name != entry.data.get(CONF_BLE_NAME):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_BLE_NAME: ble_name}
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up walkingpad from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    address = entry.data.get(CONF_MAC)

    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
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
    _async_store_ble_name(hass, entry, _advertised_name(hass, address))
    walkingpad_device = WalkingPad(name, ble_device, entry.data.get(CONF_BLE_NAME))
    coordinator = WalkingPadCoordinator(hass, walkingpad_device, entry.entry_id)
    _apply_idle_disconnect(coordinator, entry)

    integration_data: WalkingPadIntegrationData = {
        "device": walkingpad_device,
        "coordinator": coordinator,
        "options": dict(entry.options),
    }
    hass.data[DOMAIN][entry.entry_id] = integration_data

    @callback
    def _async_update_ble_device(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        if coordinator.shutting_down:
            return
        walkingpad_device.update_ble_device(service_info.device)
        ble_name = _advertised_name(hass, address)
        if ble_name and ble_name != entry.data.get(CONF_BLE_NAME):
            walkingpad_device.set_ble_name(ble_name)
        _async_store_ble_name(hass, entry, ble_name)

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_update_ble_device,
            bluetooth.BluetoothCallbackMatcher(address=address, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        integration_data: WalkingPadIntegrationData | None = hass.data[DOMAIN].pop(
            entry.entry_id, None
        )
        # Shut the coordinator down so its reconnect task and BLE
        # disconnect callback don't leak across reloads — otherwise the
        # next coordinator's events fire 2x, 3x, … per reload.
        if integration_data is not None:
            # No new connects or commands (e.g. a stop waiting for a
            # reconnect) while and after the coordinator shuts down.
            integration_data["device"].close()
            await integration_data["coordinator"].async_shutdown()

    return unload_ok


# Entity_id migration, run on every setup (idempotent). Earlier versions of this integration registered
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
    for registry_entry in entries:
        # Each unique_id is `{MAC}-{key}`. Pull the key.
        if not registry_entry.unique_id.startswith(f"{mac}-"):
            continue
        key = registry_entry.unique_id[len(mac) + 1 :]

        desired_slug = _ENTITY_ID_MIGRATIONS.get(key)
        if desired_slug is None:
            continue
        desired_entity_id = f"{registry_entry.domain}.{desired_slug}"

        new_translation_key = _TRANSLATION_KEY_MIGRATIONS.get(
            registry_entry.translation_key or "", registry_entry.translation_key
        )

        updates: dict[str, str] = {}
        if (
            registry_entry.entity_id != desired_entity_id
            and entity_registry.async_get(desired_entity_id) is None
        ):
            _LOGGER.info(
                "Migrating entity_id %s -> %s", registry_entry.entity_id, desired_entity_id
            )
            updates["new_entity_id"] = desired_entity_id
        if (
            new_translation_key is not None
            and registry_entry.translation_key != new_translation_key
        ):
            _LOGGER.info(
                "Migrating translation_key on %s: %s -> %s",
                registry_entry.entity_id,
                registry_entry.translation_key,
                new_translation_key,
            )
            updates["translation_key"] = new_translation_key

        if updates:
            try:
                entity_registry.async_update_entity(registry_entry.entity_id, **updates)
            except ValueError as err:
                # An orphan from a previous registration of the same device
                # may already occupy the canonical entity_id. Skipping the
                # rename here keeps the integration loadable; the orphan can
                # be cleaned up manually from Settings → Entities.
                _LOGGER.warning(
                    "Skipping entity_id migration for %s: %s",
                    registry_entry.entity_id,
                    err,
                )
