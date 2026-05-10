"""The Walking Pad Coordinator."""

import asyncio
import itertools
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, BeltState, WalkingPadMode, WalkingPadStatus
from .walkingpad import WalkingPad

_LOGGER = logging.getLogger(__name__)

STATUS_UPDATE_INTERVAL = timedelta(seconds=5)

# Must accommodate the library's full connect-retry cycle (FTMS: up to 5 ×
# 10 s + small gaps ≈ 55 s). Cancelling mid-cycle leaves Bleak in a flaky
# state and used to leak the WalkingPad's CONNECTING status, jamming all
# subsequent reconnects until something kicked it loose.
STATUS_UPDATE_TIMEOUT_SECONDS = 60

class WalkingPadCoordinator(DataUpdateCoordinator[WalkingPadStatus]):
    """WalkingPad coordinator."""

    def __init__(self, hass: HomeAssistant, walkingpad_device: WalkingPad) -> None:
        """Initialise WalkingPad coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            always_update=False,
            update_interval=STATUS_UPDATE_INTERVAL,
            update_method=None,
        )
        self.walkingpad_device = walkingpad_device
        self.walkingpad_device.register_status_callback(self._async_handle_update)
        self.walkingpad_device.register_disconnect_callback(self._async_handle_disconnect)
        # Single reconnect task — coordinated by _async_handle_disconnect so we
        # don't spawn parallel reconnect attempts that race each other.
        self._reconnect_task: asyncio.Task[None] | None = None
        # firmware_version is empty at entity construction time (the library
        # only reads it on connect), so the DeviceInfo snapshots in entity
        # __init__ all see ""/None. We push the real value into the device
        # registry once it's known — see _async_push_firmware_once.
        self._firmware_pushed: bool = False
        self.data = {
            "belt_state": BeltState.STOPPED,
            "speed": 0.0,
            "mode": WalkingPadMode.MANUAL,
            "session_running_time": 0,
            "session_distance": 0,
            "session_steps": 0,
            "session_calories": 0,
            "status_timestamp": 0,
        }

    async def _async_update_data(self) -> WalkingPadStatus:
        # When stay_connected is disabled, skip polling/auto-reconnect entirely.
        # Sensors will show stale data; commands handle their own connect/disconnect.
        if not self.walkingpad_device.stay_connected:
            return self.data

        async with asyncio.timeout(STATUS_UPDATE_TIMEOUT_SECONDS):
            await self.walkingpad_device.update_state()
            # We don't know the status yet, it will be transmitted to the _async_handle_update callback.
            # In the meantime, we return the current data to avoid any update (thanks to always_update=False).
            return self.data

    @property
    def connected(self) -> bool:
        """Get the device connection status."""
        return self.walkingpad_device.connected

    @callback
    def _async_handle_update(self, status: WalkingPadStatus) -> None:
        """Receive status updates from the WalkingPad controller."""
        if status.get("status_timestamp", 0) > self.data.get("status_timestamp", 0):
            _LOGGER.debug("WalkingPad status update : %s", status)
            self.async_set_updated_data(status)
        self._async_push_firmware_once()

    @callback
    def _async_push_firmware_once(self) -> None:
        """Update the device registry's sw_version once firmware is known.

        Entities snapshot ``firmware_version`` into their DeviceInfo at
        __init__ time, but the library doesn't read it until connect, so
        the device card otherwise stays blank. We push it here on the
        first status update where the library has it available.
        """
        if self._firmware_pushed:
            return
        fw = self.walkingpad_device.firmware_version
        if not fw:
            return
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, self.walkingpad_device.mac)}
        )
        if device is None:
            return
        if device.sw_version != fw:
            device_registry.async_update_device(device.id, sw_version=fw)
        self._firmware_pushed = True

    @callback
    def _async_handle_disconnect(self) -> None:
        """Update listeners and (re)start the reconnect loop if stay_connected."""
        self.async_update_listeners()
        self._ensure_reconnect_running()

    async def _reconnect_loop(self) -> None:
        """Reconnect with backoff, bailing as soon as the link is back.

        First iteration tries immediately (delay 0) — when the BLE link
        drops, the treadmill is often re-advertising within a hundred ms,
        so any wait here is pure latency. Backoff ramps up only if the
        device stays unreachable, capping at 30 s sustained — we never
        want to give up while stay_connected is on, since the treadmill
        might just be powered off and the user could turn it on at any
        moment.
        """
        for delay in itertools.chain((0, 2, 5, 10, 15), itertools.repeat(30)):
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if not self.walkingpad_device.stay_connected:
                return
            if self.walkingpad_device.connected:
                return
            _LOGGER.info("Reconnect attempt after %ss", delay)
            try:
                await self.walkingpad_device.connect()
            except Exception:
                _LOGGER.exception("Reconnect attempt failed")
            if self.walkingpad_device.connected:
                return

    @callback
    def _ensure_reconnect_running(self) -> None:
        """Spawn the reconnect loop if not already in flight."""
        if not self.walkingpad_device.stay_connected:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = self.hass.async_create_task(
            self._reconnect_loop(),
            name="walkingpad-reconnect",
        )

    async def _async_connect(self, *_) -> None:
        """Connect to the device."""
        await self.walkingpad_device.connect()

    async def _async_disconnect(self, *_) -> None:
        """Disconnect the device."""
        await self.walkingpad_device.disconnect()

    @callback
    def async_add_listener(
        self, update_callback: CALLBACK_TYPE, context: Any = None
    ) -> Callable[[], None]:
        """Listen for data updates; trigger reconnect loop on first listener."""
        if (
            not self._listeners
            and self.walkingpad_device.stay_connected
            and not self.walkingpad_device.connected
        ):
            # Use the same reconnect loop as mid-session drops, so failed initial
            # connects retry with backoff instead of becoming a one-shot fail.
            self._ensure_reconnect_running()
        return super().async_add_listener(update_callback, context)

    @callback
    def _unschedule_refresh(self) -> None:
        """Stop polling when no listeners remain.

        Only disconnect the BLE link if Stay-connected is OFF — when it's
        ON the user has explicitly opted in to a persistent connection
        and we should keep it alive even while they're on a different
        HA page (so coming back doesn't show a disconnected device).
        """
        if not self.walkingpad_device.stay_connected:
            async_call_later(
                self.hass,
                0,
                HassJob(self._async_disconnect, "Disconnect the WalkingPad"),
            )
        return super()._unschedule_refresh()

    async def async_shutdown(self) -> None:
        """Cancel reconnect task and disconnect on integration unload.

        Without this, a reload would leave the old coordinator's reconnect
        task and BLE callbacks alive, so a new coordinator's events fire
        twice (or more, after multiple reloads).
        """
        await self._cancel_reconnect_task()
        await super().async_shutdown()
        await self._async_disconnect()

    async def async_set_stay_connected(self, value: bool) -> None:
        """Handle the stay_connected toggle from the switch entity.

        When turned ON: route through the reconnect loop so failed connects
        retry with backoff instead of being a one-shot fail.
        When turned OFF: cancel any in-flight reconnect and disconnect.
        """
        self.walkingpad_device.stay_connected = value
        if value:
            self._ensure_reconnect_running()
        else:
            await self._cancel_reconnect_task()
            await self._async_disconnect()

    async def _cancel_reconnect_task(self) -> None:
        """Cancel the reconnect loop and wait for it to actually finish.

        ``cancel()`` only marks the task; the cancellation propagates on
        the next event-loop tick. Without awaiting we'd race the in-flight
        ``walkingpad.connect()`` against the immediate ``disconnect()``
        that follows, both touching ``_connection_status``.
        """
        task = self._reconnect_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
