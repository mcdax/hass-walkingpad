"""Walkingpad switch support."""

import logging
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import WalkingPadIntegrationData
from .const import (
    CONF_PREFERRED_MODE,
    CONF_REMOTE_CONTROL_ENABLED,
    DEFAULT_PREFERRED_MODE,
    DOMAIN,
    PREFERRED_MODE_MANUAL,
    BeltState,
    WalkingPadMode,
)
from .coordinator import STATUS_UPDATE_INTERVAL, WalkingPadCoordinator
from .entity import (
    WalkingPadPushEntity,
    async_remove_stale_entities,
    walkingpad_device_info,
    walkingpad_unique_id,
)
from .utils import TemporaryValue

_LOGGER = logging.getLogger(__name__)

BELT_SWITCH_KEY = "walkingpad_belt_switch"
STAY_CONNECTED_KEY = "walkingpad_stay_connected"
SOUND_KEY = "walkingpad_sound"

# Both belt switch variants (manual / auto) use the unified "Belt"
# translation_key. The mode is internal config — surfacing it in the entity
# name caused confusion when the entity_id, friendly_name and translation_key
# diverged after a mode change.
BELT_SWITCH_DESCRIPTION = SwitchEntityDescription(
    device_class=SwitchDeviceClass.SWITCH,
    icon="mdi:cog-play",
    key=BELT_SWITCH_KEY,
    translation_key="walkingpad_belt",
    has_entity_name=True,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the WalkingPad switch."""
    entry_data: WalkingPadIntegrationData = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["coordinator"]

    entities: list[SwitchEntity] = []

    # Stay Connected switch is always created (not gated by remote_control)
    entities.append(WalkingPadStayConnectedSwitch(coordinator))

    # Sound is a device setting, not belt control: not gated either.
    # After a restart the BLE name may not be known yet; keep the entity then.
    if coordinator.walkingpad_device.supports_sound or er.async_get(
        hass
    ).async_get_entity_id(
        "switch",
        DOMAIN,
        walkingpad_unique_id(coordinator.walkingpad_device, SOUND_KEY),
    ):
        entities.append(WalkingPadSoundSwitch(coordinator))

    # Belt control switches are gated by remote_control_enabled
    if entry.options.get(CONF_REMOTE_CONTROL_ENABLED, False):
        preferred_mode = entry.options.get(CONF_PREFERRED_MODE, DEFAULT_PREFERRED_MODE)

        if preferred_mode == PREFERRED_MODE_MANUAL:
            entities.append(WalkingPadBeltSwitchManual(coordinator))
        else:
            entities.append(WalkingPadBeltSwitchAuto(coordinator))
    else:
        # Clean up belt switch entity if remote_control was disabled
        async_remove_stale_entities(hass, entry, "switch", (BELT_SWITCH_KEY,))

    async_add_entities(entities)


class WalkingPadBeltSwitchBase(SwitchEntity):
    """Base class for WalkingPad belt switch entities."""

    entity_description: SwitchEntityDescription
    coordinator: WalkingPadCoordinator
    _temporary_belt_state: TemporaryValue[BeltState]
    _temporary_mode: TemporaryValue[WalkingPadMode]

    # We push state updates from the coordinator listener; HA polling would
    # otherwise lag the belt-state by many seconds after a slider drag, so
    # the toggle would stay in the wrong position until the next poll.
    _attr_should_poll = False

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates.

        is_on then re-evaluates promptly whenever the underlying belt_state
        changes, matching how the State sensor refreshes.
        """
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def available(self) -> bool:
        """The belt switch stays available even while the BLE link is down.

        Tapping it triggers the WalkingPad wrapper's connect-and-issue
        sequence (same pattern as the speed slider), so the user can
        start/stop the belt directly from the toggle without first
        re-enabling Stay-connected. Mirrors the slider's #9 behavior.
        """
        return True

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the belt switch."""
        self._temporary_belt_state = TemporaryValue[BeltState]()
        self._temporary_mode = TemporaryValue[WalkingPadMode]()
        self.coordinator = coordinator
        self.entity_description = BELT_SWITCH_DESCRIPTION
        self._attr_unique_id = walkingpad_unique_id(
            coordinator.walkingpad_device, BELT_SWITCH_KEY
        )
        self._attr_suggested_object_id = BELT_SWITCH_KEY
        self._attr_device_info = walkingpad_device_info(coordinator.walkingpad_device)
        super().__init__()

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        current_timestamp = self.coordinator.data.get("status_timestamp", 0)
        current_belt_state = self.coordinator.data.get("belt_state")

        # Check expiration for temporary belt state with special condition
        # Don't reset if belt is starting (to keep temporary state during startup)
        if (
            self._temporary_belt_state.has_value
            and current_belt_state != BeltState.STARTING
            and current_timestamp > self._temporary_belt_state.expiration_timestamp
        ):
            self._temporary_belt_state.reset()

        # Use temporary value if available (without auto-expiration check),
        # otherwise use current state
        belt_state = self._temporary_belt_state.peek(
            current_belt_state or BeltState.STOPPED
        )

        return belt_state in [BeltState.ACTIVE, BeltState.STARTING]

    def _temporary_expiration(self) -> float:
        """Expiry for a temporary value: one status interval after the last status."""
        return (
            self.coordinator.data.get("status_timestamp", 0)
            + STATUS_UPDATE_INTERVAL.total_seconds()
        )

    def set_temporary_belt_state(self, belt_state: BeltState) -> None:
        """Set a temporary belt state."""
        self._temporary_belt_state.set(belt_state, self._temporary_expiration())

    def set_temporary_mode(self, mode: WalkingPadMode) -> None:
        """Set a temporary mode."""
        self._temporary_mode.set(mode, self._temporary_expiration())

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        raise NotImplementedError

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        raise NotImplementedError


class WalkingPadBeltSwitchManual(WalkingPadBeltSwitchBase):
    """Represent the WalkingPad belt switch in manual mode."""

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on.

        Stay-connected is left as the user set it. If they had it off,
        the WalkingPad wrapper connects for the start command and drops
        the link 5 s after the last command; the belt keeps running, but
        the live sensors are unavailable while disconnected.
        """
        self.set_temporary_mode(WalkingPadMode.MANUAL)
        self.set_temporary_belt_state(BeltState.STARTING)
        current_mode = self.coordinator.data.get("mode")
        if current_mode != WalkingPadMode.MANUAL:
            await self.coordinator.walkingpad_device.start_belt_in_mode(
                WalkingPadMode.MANUAL
            )
        else:
            await self.coordinator.walkingpad_device.start_belt()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off — sends PAUSE, not STOP.

        The phone app and the physical remote pause when the user presses
        their stop button: the device decelerates to zero but the session
        counters (time, distance, calories, steps) carry over so a
        subsequent toggle-on resumes from where the user left off.

        ``stop_belt()`` (the hard reset that zeros counters) is still
        available on the WalkingPad wrapper if a custom action / service
        wants it — it's intentionally not on the default toggle because
        most users expect the toggle to behave like the app does.
        Stay-connected is left untouched.
        """
        self.set_temporary_belt_state(BeltState.STOPPED)
        await self.coordinator.walkingpad_device.pause_belt()


class WalkingPadBeltSwitchAuto(WalkingPadBeltSwitchBase):
    """Represent the WalkingPad belt switch in auto mode."""

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        current_timestamp = self.coordinator.data.get("status_timestamp", 0)
        current_mode = self.coordinator.data.get("mode")
        current_belt_state = self.coordinator.data.get("belt_state")

        mode = self._temporary_mode.get(
            current_timestamp, current_mode or WalkingPadMode.MANUAL
        )
        belt_state = self._temporary_belt_state.get(
            current_timestamp, current_belt_state or BeltState.STOPPED
        )

        if mode == WalkingPadMode.AUTO:
            return True
        if mode == WalkingPadMode.STANDBY:
            return False
        return belt_state in [BeltState.ACTIVE, BeltState.STARTING]

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on (auto mode).  Stay-connected is left untouched."""
        self.set_temporary_mode(WalkingPadMode.AUTO)
        await self.coordinator.walkingpad_device.switch_mode(WalkingPadMode.AUTO)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off (auto mode → standby).

        Stay-connected is left untouched — fully under the user's control.
        """
        self.set_temporary_mode(WalkingPadMode.STANDBY)
        self.set_temporary_belt_state(BeltState.STOPPED)
        await self.coordinator.walkingpad_device.switch_mode(WalkingPadMode.STANDBY)


class WalkingPadStayConnectedSwitch(SwitchEntity, RestoreEntity):
    """Switch to control whether HA maintains a persistent BLE connection.

    When ON (default): HA stays connected, coordinator polls every 5s, sensors
    update live.  This is the normal behavior.

    When OFF: HA disconnects immediately, freeing the BLE link so the user's
    smartphone app (e.g. KS Fit) can connect for tracking. Commands (belt
    on/off, speed) still work — they connect, send the command, and drop the
    link 5 s after the last one. Live sensors are unavailable while
    disconnected.

    State is persisted across HA restarts via RestoreEntity.
    """

    entity_description: SwitchEntityDescription

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the stay connected switch."""
        self.coordinator = coordinator
        self.entity_description = SwitchEntityDescription(
            device_class=SwitchDeviceClass.SWITCH,
            icon="mdi:bluetooth-connect",
            key=STAY_CONNECTED_KEY,
            translation_key=STAY_CONNECTED_KEY,
            has_entity_name=True,
        )
        self._attr_unique_id = walkingpad_unique_id(
            coordinator.walkingpad_device, STAY_CONNECTED_KEY
        )
        self._attr_suggested_object_id = STAY_CONNECTED_KEY
        self._attr_device_info = walkingpad_device_info(coordinator.walkingpad_device)
        super().__init__()

    @property
    def is_on(self) -> bool:
        """Return the live stay_connected state from the device."""
        return self.coordinator.walkingpad_device.stay_connected

    async def async_added_to_hass(self) -> None:
        """Restore state after HA restart and subscribe to coordinator updates."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            restored = last_state.state == "on"
            _LOGGER.info(
                "Restoring stay_connected state: %s (was %s)",
                restored,
                last_state.state,
            )
            self.coordinator.walkingpad_device.stay_connected = restored
        else:
            _LOGGER.info("No previous stay_connected state, defaulting to ON")
            self.coordinator.walkingpad_device.stay_connected = True

        # Subscribe to coordinator updates so our UI state refreshes
        # whenever the underlying stay_connected flag changes.
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_state_change)
        )

    @callback
    def _handle_state_change(self) -> None:
        """React to coordinator data updates by refreshing our HA state."""
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on — re-enable persistent BLE connection."""
        await self.coordinator.async_set_stay_connected(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off — disconnect BLE to free link for phone app."""
        await self.coordinator.async_set_stay_connected(False)
        self.async_write_ha_state()


class WalkingPadSoundSwitch(WalkingPadPushEntity, SwitchEntity, RestoreEntity):
    """Switch for the treadmill's buzzer.

    The treadmill remembers the setting. It is only read back when switching
    (reading it stops the treadmill's idle data), so a change made in the
    KS Fit app isn't noticed: the state is the one last set from here.
    """

    _attr_assumed_state = True

    entity_description = SwitchEntityDescription(
        key=SOUND_KEY,
        icon="mdi:volume-high",
        translation_key=SOUND_KEY,
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the sound switch."""
        super().__init__(coordinator, SOUND_KEY)
        self._attr_is_on: bool | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last state set from here."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in (STATE_ON, STATE_OFF):
            self._attr_is_on = last_state.state == STATE_ON

    async def _async_set_sound(self, on: bool) -> None:
        await self.coordinator.walkingpad_device.set_sound(on)
        self._attr_is_on = on
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the buzzer on."""
        await self._async_set_sound(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the buzzer off."""
        await self._async_set_sound(False)
