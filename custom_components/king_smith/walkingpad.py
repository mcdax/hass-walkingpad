"""Walking Pad API - Thin wrapper around walkingpad-controller library.

This module provides a WalkingPad class that wraps the walkingpad-controller
library's WalkingPadController, adding Home Assistant-specific features:
  - WalkingPadConnectionStatus enum (for coordinator)
  - stay_connected toggle (persistent BLE vs. connect-per-command)
  - Status mapping from TreadmillStatus -> WalkingPadStatus (TypedDict)

Protocol detection, BLE connection, FTMS/WiLink delegation, cold-start
recovery, and pending speed management are all handled by the library.
"""

import asyncio
import logging
from enum import Enum, unique

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from walkingpad_controller import (
    BeltState,
    OperatingMode,
    TreadmillStatus,
    WalkingPadController,
)

from .const import WalkingPadMode, WalkingPadStatus

_LOGGER = logging.getLogger(__name__)

# When stay_connected is OFF, disconnect this many seconds after the most
# recent action. Bursts of actions (e.g. start_belt immediately followed by
# set_speed) reset the timer so the BLE link is held for the whole burst,
# avoiding connect/disconnect churn between them.
IDLE_DISCONNECT_TIMEOUT_SECONDS = 5.0


@unique
class WalkingPadConnectionStatus(Enum):
    """An enumeration of the possible connection states."""

    NOT_CONNECTED = 0
    CONNECTING = 1
    CONNECTED = 2


class WalkingPad:
    """Home Assistant wrapper around WalkingPadController.

    Adds HA-specific features (stay_connected, connection status enum,
    WalkingPadStatus mapping) on top of the library's unified controller.
    """

    def __init__(self, name: str, ble_device: BLEDevice) -> None:
        """Create a WalkingPad object."""
        self._controller = WalkingPadController(ble_device=ble_device, name=name)
        self._callbacks = []
        self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED

        # BLE operation lock — serialises all BLE operations so coordinator
        # polls cannot interleave with multi-step command sequences
        # (e.g. switch_mode + sleep + start_belt in manual-mode turn-on).
        self._ble_lock = asyncio.Lock()

        # Stay Connected toggle: when True (default), HA maintains a
        # persistent BLE connection and the coordinator polls every 5s.
        # When False, HA disconnects 5s after the last action, freeing
        # the BLE link so the user's smartphone app can connect.
        self._stay_connected: bool = True

        # Idle-disconnect timer task — schedules a `disconnect()` call
        # IDLE_DISCONNECT_TIMEOUT_SECONDS after the most recent command,
        # when stay_connected is False. Each new command cancels the
        # current timer and (post-command) schedules a fresh one.
        self._disconnect_timer_task: asyncio.Task[None] | None = None

        # Register library callbacks
        self._controller.register_status_callback(self._on_library_status_update)
        self._controller.register_disconnect_callback(self._on_library_disconnect)

    # --- Status mapping ---

    def _on_library_status_update(self, status: TreadmillStatus) -> None:
        """Map library TreadmillStatus to HA WalkingPadStatus and fire callbacks."""
        belt_state = (
            BeltState(status.belt_state)
            if status.belt_state in iter(BeltState)
            else BeltState.UNKNOWN
        )

        # Map library OperatingMode int to HA WalkingPadMode enum
        try:
            mode = WalkingPadMode(status.mode)
        except ValueError:
            mode = WalkingPadMode.MANUAL

        ha_status: WalkingPadStatus = {
            "belt_state": belt_state,
            "speed": status.speed,
            "mode": mode,
            "session_distance": status.distance,
            "session_running_time": status.duration,
            "session_steps": status.steps,
            "session_calories": status.calories,
            "training_status": status.training_status,
            "last_fm_event": status.last_fm_event,
            "status_timestamp": status.timestamp,
        }
        self._fire_callbacks(ha_status)

    def _on_library_disconnect(self) -> None:
        """Handle disconnect from the library controller."""
        _LOGGER.warning("Device disconnected, marking as not connected")
        self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED

    def _fire_callbacks(self, status: WalkingPadStatus) -> None:
        """Fire all registered status callbacks."""
        for callback in self._callbacks:
            try:
                callback(status)
            except Exception:
                _LOGGER.exception("Error in status callback")

    def register_status_callback(self, callback) -> None:
        """Register a status callback."""
        self._callbacks.append(callback)

    # --- Properties ---

    @property
    def mac(self):
        """Mac address."""
        return self._controller.address

    @property
    def name(self):
        """Name."""
        return self._controller.name

    @property
    def protocol(self):
        """The detected protocol type."""
        return self._controller.protocol

    @property
    def connection_status(self) -> WalkingPadConnectionStatus:
        """Connection status."""
        return self._connection_status

    @property
    def connected(self) -> bool:
        """Boolean property to check if the device is connected."""
        return self._connection_status == WalkingPadConnectionStatus.CONNECTED

    @property
    def ble_lock(self) -> asyncio.Lock:
        """BLE operation lock for multi-step command sequences."""
        return self._ble_lock

    @property
    def stay_connected(self) -> bool:
        """Whether HA should maintain a persistent BLE connection."""
        return self._stay_connected

    @stay_connected.setter
    def stay_connected(self, value: bool) -> None:
        """Set the stay_connected preference."""
        self._stay_connected = value
        # Either direction invalidates a pending idle-disconnect:
        #   True  → user wants to stay connected, don't drop the link
        #   False → coordinator's set_stay_connected(False) disconnects
        #           directly, no need for the deferred timer
        self._cancel_idle_disconnect()
        _LOGGER.info("Stay connected set to %s", value)

    async def disconnect_after_command(self) -> None:
        """Schedule a deferred disconnect when stay_connected is OFF.

        Bursts of actions reset the timer, so a sequence like
        `start_belt -> set_speed` doesn't disconnect-and-reconnect
        between the two commands. After the timer fires (5s after
        the last action), we disconnect — unless stay_connected has
        been turned back on or another action arrived first.
        """
        if self._stay_connected:
            return
        if not self.connected:
            return
        _LOGGER.debug(
            "Stay connected disabled, scheduling disconnect in %ss",
            IDLE_DISCONNECT_TIMEOUT_SECONDS,
        )
        self._reschedule_idle_disconnect()

    def _reschedule_idle_disconnect(self) -> None:
        """Cancel any pending idle-disconnect and start a fresh timer."""
        self._cancel_idle_disconnect()
        self._disconnect_timer_task = asyncio.create_task(
            self._idle_disconnect_after_timeout(),
            name="walkingpad-idle-disconnect",
        )

    def _cancel_idle_disconnect(self) -> None:
        """Cancel the idle-disconnect timer if it's pending."""
        task = self._disconnect_timer_task
        if task is not None and not task.done():
            task.cancel()
        self._disconnect_timer_task = None

    async def _idle_disconnect_after_timeout(self) -> None:
        """Wait for the idle timeout, then disconnect under the BLE lock."""
        try:
            await asyncio.sleep(IDLE_DISCONNECT_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        # Re-check under the BLE lock so we don't race with a command
        # that started during the sleep.
        async with self._ble_lock:
            if self._stay_connected:
                _LOGGER.debug("Idle timer fired but stay_connected is on; skipping")
                return
            if not self.connected:
                return
            _LOGGER.info(
                "Idle timeout reached after %ss, disconnecting",
                IDLE_DISCONNECT_TIMEOUT_SECONDS,
            )
            await self.disconnect()

    # --- Connection ---

    async def connect(self) -> None:
        """Connect to the device."""
        # An incoming connection request invalidates any pending
        # idle-disconnect — we're about to use the link.
        self._cancel_idle_disconnect()

        if self._connection_status == WalkingPadConnectionStatus.CONNECTING:
            _LOGGER.info("Already connecting to WalkingPad")
            return

        self._connection_status = WalkingPadConnectionStatus.CONNECTING

        try:
            await self._controller.connect()
            self._connection_status = WalkingPadConnectionStatus.CONNECTED
            _LOGGER.info(
                "Connected to WalkingPad via %s protocol",
                self._controller.protocol.value,
            )
        except (BleakError, TimeoutError) as err:
            _LOGGER.warning("Unable to connect to WalkingPad: %s", err)
            self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED
        except Exception:
            _LOGGER.exception("Unable to connect to WalkingPad")
            self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED

    async def disconnect(self) -> None:
        """Disconnect the device."""
        if self._connection_status == WalkingPadConnectionStatus.NOT_CONNECTED:
            return
        try:
            await self._controller.disconnect()
        except Exception:
            _LOGGER.exception("Error during disconnect")
        finally:
            self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED

    # --- Commands ---

    async def update_state(self) -> None:
        """Update device state by requesting current status."""
        async with self._ble_lock:
            if self._connection_status == WalkingPadConnectionStatus.NOT_CONNECTED:
                await self.connect()

            if not self.connected:
                return

            try:
                await self._controller.update_state()
                # If the library detects the backend is disconnected, it sets
                # connected=False. Sync our status.
                if not self._controller.connected:
                    self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED
            except BleakError as err:
                _LOGGER.warning("Bluetooth error during state update: %s", err)
                self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED

    async def start_belt(self) -> None:
        """Start the belt."""
        async with self._ble_lock:
            await self._start_belt_unlocked()

    async def _start_belt_unlocked(self) -> None:
        """Start the belt (caller must hold _ble_lock)."""
        if self._connection_status == WalkingPadConnectionStatus.NOT_CONNECTED:
            await self.connect()
        if not self.connected:
            return

        try:
            await self._controller.start()
        except BleakError as err:
            _LOGGER.warning("Bluetooth error: %s", err)
            self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED
        finally:
            await self.disconnect_after_command()

    async def stop_belt(self) -> None:
        """Stop the belt."""
        async with self._ble_lock:
            if self._connection_status == WalkingPadConnectionStatus.NOT_CONNECTED:
                await self.connect()
            if not self.connected:
                return

            try:
                await self._controller.stop()
            except BleakError as err:
                _LOGGER.warning("Bluetooth error: %s", err)
                self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED
            finally:
                await self.disconnect_after_command()

    async def set_speed(self, speed: float) -> None:
        """Set the belt speed in km/h."""
        async with self._ble_lock:
            if self._connection_status == WalkingPadConnectionStatus.NOT_CONNECTED:
                await self.connect()
            if not self.connected:
                return

            try:
                await self._controller.set_speed(speed)
            except BleakError as err:
                _LOGGER.warning("Bluetooth error: %s", err)
                self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED
            finally:
                await self.disconnect_after_command()

    async def switch_mode(self, mode: WalkingPadMode) -> None:
        """Switch the WalkingPad mode."""
        async with self._ble_lock:
            await self._switch_mode_unlocked(mode)

    async def _switch_mode_unlocked(self, mode: WalkingPadMode) -> None:
        """Switch the WalkingPad mode (caller must hold _ble_lock)."""
        if self._connection_status == WalkingPadConnectionStatus.NOT_CONNECTED:
            await self.connect()
        if not self.connected:
            return

        try:
            # Map HA WalkingPadMode to library OperatingMode
            lib_mode = OperatingMode(mode.value)
            await self._controller.switch_mode(lib_mode)
        except BleakError as err:
            _LOGGER.warning("Bluetooth error: %s", err)
            self._connection_status = WalkingPadConnectionStatus.NOT_CONNECTED
        finally:
            await self.disconnect_after_command()

    async def start_belt_in_mode(self, mode: WalkingPadMode) -> None:
        """Switch mode and start the belt atomically.

        Holds the BLE lock across the entire sequence so coordinator polls
        cannot interleave between switch_mode and start_belt.
        """
        async with self._ble_lock:
            await self._switch_mode_unlocked(mode)
            await asyncio.sleep(1.5)
            await self._start_belt_unlocked()

    # --- Speed properties (delegated to library) ---

    @property
    def min_speed(self) -> float:
        """Minimum speed in km/h."""
        return self._controller.min_speed

    @property
    def max_speed(self) -> float:
        """Maximum speed in km/h."""
        return self._controller.max_speed

    @property
    def speed_increment(self) -> float:
        """Speed step size in km/h."""
        return self._controller.speed_increment

    @property
    def firmware_version(self) -> str:
        """Firmware version reported by the device (FTMS only; empty otherwise)."""
        return self._controller.firmware_version
