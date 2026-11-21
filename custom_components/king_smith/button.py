"""WalkingPad button support — Stop Session and Reconnect buttons."""

from __future__ import annotations

from homeassistant.components.button import (
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WalkingPadIntegrationData
from .const import CONF_REMOTE_CONTROL_ENABLED, DOMAIN
from .coordinator import WalkingPadCoordinator
from .entity import WalkingPadPushEntity, async_remove_stale_entities


STOP_SESSION_KEY = "walkingpad_stop_session"
RECONNECT_KEY = "walkingpad_reconnect"


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

    # The Reconnect button is always created — it's about connectivity, not
    # belt control, so it isn't gated by remote_control (same reasoning as
    # the Stay-connected switch).
    async_add_entities([WalkingPadReconnectButton(coordinator)])

    if entry.options.get(CONF_REMOTE_CONTROL_ENABLED, False):
        async_add_entities([WalkingPadStopSessionButton(coordinator)])
        return

    # Remote control was disabled — clean up the entity if it existed
    # from a previous configuration.
    async_remove_stale_entities(hass, entry, "button", (STOP_SESSION_KEY,))


class WalkingPadStopSessionButton(WalkingPadPushEntity, ButtonEntity):
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

    # No entity_category — the Stop button is a primary user-facing control,
    # so it lives under Controls alongside the belt switch and speed slider,
    # not under Diagnostic.
    entity_description = ButtonEntityDescription(
        key=STOP_SESSION_KEY,
        icon="mdi:stop",
        translation_key=STOP_SESSION_KEY,
    )

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the Stop button."""
        super().__init__(coordinator, STOP_SESSION_KEY)

    @property
    def available(self) -> bool:
        """The button is only enabled when the BLE link is up."""
        return self.coordinator.connected

    async def async_press(self) -> None:
        """Send a hard stop — full session end, counters reset."""
        await self.coordinator.walkingpad_device.stop_belt()


class WalkingPadReconnectButton(WalkingPadPushEntity, ButtonEntity):
    """Force an immediate BLE reconnect attempt.

    Addresses the "reconnect takes a while / feels random" pain (issue #4):
    when the treadmill has been off, HA's reconnect loop settles into a 30 s
    backoff, so it can lag noticeably behind the user powering the belt on.
    Pressing this button short-circuits that wait and connects right away —
    the equivalent of the "reconnect" affordance in the KS Fit app.

    Only enabled while the link is down; once connected there's nothing to
    do, so the button greys out (the inverse of the Stop button).
    """

    # Connectivity housekeeping rather than a primary workout control, so it
    # lives under Diagnostic next to the Connected sensor.
    entity_description = ButtonEntityDescription(
        key=RECONNECT_KEY,
        icon="mdi:bluetooth-connect",
        translation_key=RECONNECT_KEY,
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    def __init__(self, coordinator: WalkingPadCoordinator) -> None:
        """Initialize the Reconnect button."""
        super().__init__(coordinator, RECONNECT_KEY)

    @property
    def available(self) -> bool:
        """Only offer a reconnect while the link is down."""
        return not self.coordinator.connected

    async def async_press(self) -> None:
        """Kick an immediate reconnect attempt."""
        await self.coordinator.async_reconnect_now()
