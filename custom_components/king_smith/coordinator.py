"""The Walking Pad Coordinator."""

import asyncio
import itertools
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DEFAULT_IDLE_DISCONNECT_MINUTES,
    DOMAIN,
    BeltState,
    WalkingPadMode,
    WalkingPadStatus,
)
from .walkingpad import WalkingPad

_LOGGER = logging.getLogger(__name__)

STATUS_UPDATE_INTERVAL = timedelta(seconds=5)

# Generous upper bound for one poll. It was sized for the library's full
# connect-retry cycle (FTMS: up to 5 × 10 s + small gaps ≈ 55 s); the
# integration now caps the library at one attempt (see walkingpad.py), but
# the margin is kept. Cancelling mid-cycle leaves Bleak in a flaky state and
# used to leak the WalkingPad's CONNECTING status, jamming all subsequent
# reconnects until something kicked it loose.
STATUS_UPDATE_TIMEOUT_SECONDS = 60

# Reconnect backoff: try right away, ramp up, then retry every 30 s for as
# long as stay_connected is on.
RECONNECT_INITIAL_DELAYS_SECONDS = (0, 2, 5, 10, 15)
RECONNECT_SUSTAINED_DELAY_SECONDS = 30


def _device_by_identifier(
    device_registry: dr.DeviceRegistry,
    identifier: tuple[str, str],
    config_entry_id: str | None,
) -> dr.DeviceEntry | None:
    """Look up this entry's device by identifier.

    Newer cores deprecate ``async_get_device`` (identifiers are only unique
    within a config entry) in favour of a lookup scoped to the entry; older
    cores only have the unscoped call.
    """
    lookup = getattr(device_registry, "async_get_device_by_identifier", None)
    if lookup is not None and config_entry_id is not None:
        return lookup(identifier, config_entry_id)
    return device_registry.async_get_device(identifiers={identifier})


class WalkingPadCoordinator(DataUpdateCoordinator[WalkingPadStatus]):
    """WalkingPad coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        walkingpad_device: WalkingPad,
        entry_id: str | None = None,
    ) -> None:
        """Initialise WalkingPad coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            always_update=False,
            update_interval=STATUS_UPDATE_INTERVAL,
        )
        self.walkingpad_device = walkingpad_device
        # Owning config entry, for the entry-scoped device-registry lookup.
        self._entry_id = entry_id
        # Set by async_shutdown(); no reconnects after that.
        self._shutting_down = False
        self.walkingpad_device.register_status_callback(self._async_handle_update)
        self.walkingpad_device.register_disconnect_callback(self._async_handle_disconnect)
        # Idle disconnect (see _async_check_idle): the configured minutes, when
        # the belt was last seen moving, and whether the link was released.
        self._idle_disconnect_minutes: int = DEFAULT_IDLE_DISCONNECT_MINUTES
        self._last_active_at: float = time.monotonic()
        self._idle_released: bool = False
        self._idle_check_running: bool = False
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
            "vibration_level": 0,
            "incline": 0,
            "status_timestamp": 0,
        }

    async def _async_update_data(self) -> WalkingPadStatus:
        # When stay_connected is disabled, skip polling/auto-reconnect entirely.
        # Sensors will show stale data; commands handle their own connect/disconnect.
        if not self.walkingpad_device.stay_connected:
            return self.data

        # Safety net for silently-dropped links: the reconnect loop is normally
        # started by the BLE disconnect callback, but if a link dies without
        # that callback firing (seen on flaky BT proxies), nothing would re-arm
        # it. This periodic poll notices we're down and (idempotently) ensures
        # the reconnect loop is running.
        if not self.walkingpad_device.connected:
            if not self._idle_released:
                self._last_active_at = time.monotonic()
            self._ensure_reconnect_running()
        elif await self._async_check_idle():
            return self.data

        async with asyncio.timeout(STATUS_UPDATE_TIMEOUT_SECONDS):
            await self.walkingpad_device.update_state()
            # We don't know the status yet, it will be transmitted to the _async_handle_update callback.
            # In the meantime, we return the current data to avoid any update (thanks to always_update=False).
            return self.data

    @callback
    def set_idle_disconnect_minutes(self, minutes: int) -> None:
        """Apply the idle-disconnect option (0 = off)."""
        self._idle_disconnect_minutes = minutes
        if self._idle_released and not self._is_idle():
            self._idle_released = False
            self._ensure_reconnect_running()

    def _is_idle(self) -> bool:
        """Return True once belt and commands were inactive for long enough."""
        if (
            not self._idle_disconnect_minutes
            or not self.walkingpad_device.stay_connected
        ):
            return False
        if self.data.get("belt_state") in (BeltState.ACTIVE, BeltState.STARTING):
            return False
        last = max(self._last_active_at, self.walkingpad_device.last_command_at)
        return time.monotonic() - last >= self._idle_disconnect_minutes * 60

    async def _async_check_idle(self) -> bool:
        """Release an idle link and return True if it was released.

        The KS-Z1D resets itself and beeps every 10 minutes while it stands
        still with a connection, but not without one. The reconnect loop
        stays off until a command, the Reconnect button or the
        Stay-connected switch brings the link back.
        """
        if self._idle_check_running or not self.walkingpad_device.connected:
            # Disconnecting can itself deliver a last status update.
            return False
        # Connected again (e.g. by a command): a later drop reconnects.
        self._idle_released = False
        if not self._is_idle():
            return False
        self._idle_check_running = True
        try:
            # Before disconnecting, so the disconnect callback doesn't reconnect.
            self._idle_released = True
            if not await self.walkingpad_device.disconnect_if(self._is_idle):
                self._idle_released = False
                return False
        finally:
            self._idle_check_running = False
        _LOGGER.info(
            "Belt idle for %s min, released the connection",
            self._idle_disconnect_minutes,
        )
        self.async_update_listeners()
        return True

    @property
    def shutting_down(self) -> bool:
        """True once the entry is being unloaded."""
        return self._shutting_down

    @property
    def connected(self) -> bool:
        """Get the device connection status."""
        return self.walkingpad_device.connected

    @callback
    def _async_handle_update(self, status: WalkingPadStatus) -> None:
        """Receive status updates from the WalkingPad controller."""
        if status.get("status_timestamp", 0) > self.data.get("status_timestamp", 0):
            _LOGGER.debug("WalkingPad status update : %s", status)
            if BeltState.ACTIVE in (status["belt_state"], self.data.get("belt_state")):
                self._last_active_at = time.monotonic()
            self.async_set_updated_data(status)
            # Every status update postpones the poll, so a treadmill that
            # pushes once per second never gets polled; check here as well.
            if self._is_idle() and not self._idle_check_running:
                self.hass.async_create_task(
                    self._async_check_idle(), name="walkingpad-idle-check"
                )
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
        firmware_version = self.walkingpad_device.firmware_version
        if not firmware_version:
            return
        device_registry = dr.async_get(self.hass)
        device = _device_by_identifier(
            device_registry, (DOMAIN, self.walkingpad_device.mac), self._entry_id
        )
        if device is None:
            return
        if device.sw_version != firmware_version:
            device_registry.async_update_device(device.id, sw_version=firmware_version)
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
        for delay in itertools.chain(
            RECONNECT_INITIAL_DELAYS_SECONDS,
            itertools.repeat(RECONNECT_SUSTAINED_DELAY_SECONDS),
        ):
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if self._shutting_down or not self.walkingpad_device.stay_connected:
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
        if self._shutting_down or not self.walkingpad_device.stay_connected:
            return
        if self._idle_released:
            # A command since the release (its last_command_at is newer than
            # the belt's idle period) wants the link back, even if it dropped
            # again before the next poll.
            if self._is_idle():
                return
            self._idle_released = False
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = self.hass.async_create_task(
            self._reconnect_loop(),
            name="walkingpad-reconnect",
        )

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
        and we keep it alive even when no entity is listening (e.g. while
        the platforms reload after an options change).
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
        # Before disconnecting: the disconnect callback would otherwise
        # start a new reconnect loop for the unloaded entry.
        self._shutting_down = True
        await self._cancel_reconnect_task()
        await super().async_shutdown()
        await self._async_disconnect()

    async def async_reconnect_now(self) -> None:
        """Force an immediate reconnect attempt (from the Reconnect button).

        The reconnect loop already retries on its own, but after a few
        failures it settles into a 30 s backoff sleep — so when the user
        powers the treadmill back on, HA can take up to half a minute to
        notice. This method short-circuits that wait: it cancels the
        in-flight loop (which may be mid-sleep), connects right away, then
        re-arms the loop if we're still down and stay_connected is on so a
        failed press keeps retrying with backoff.

        Works regardless of stay_connected — a manual press is an explicit
        "connect now", the same affordance the KS Fit app exposes.
        """
        await self._cancel_reconnect_task()
        self._idle_released = False
        self._last_active_at = time.monotonic()
        await self.walkingpad_device.connect()
        self.async_update_listeners()
        if not self.walkingpad_device.connected:
            self._ensure_reconnect_running()

    async def async_set_stay_connected(self, value: bool) -> None:
        """Handle the stay_connected toggle from the switch entity.

        When turned ON: route through the reconnect loop so failed connects
        retry with backoff instead of being a one-shot fail.
        When turned OFF: cancel any in-flight reconnect and disconnect.
        """
        self.walkingpad_device.stay_connected = value
        self._idle_released = False
        self._last_active_at = time.monotonic()
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
        try:  # noqa: SIM105 - keep exact except semantics (no exception groups)
            await task
        except (asyncio.CancelledError, Exception):
            pass
