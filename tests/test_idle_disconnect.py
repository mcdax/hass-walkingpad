"""Releasing the link while the belt stands still."""

from __future__ import annotations

import asyncio
import time
import types

import pytest

from king_smith import const, coordinator as coordinator_mod, walkingpad
from test_smoke import make_pad


def make_coordinator(minutes=5, idle_for=301, belt=const.BeltState.STOPPED):
    pad = make_pad(walkingpad.WalkingPadConnectionStatus.CONNECTED, False, [])
    pad._last_command_at = float("-inf")
    coord = object.__new__(coordinator_mod.WalkingPadCoordinator)
    coord.walkingpad_device = pad
    coord.data = {"belt_state": belt}
    coord._shutting_down = False
    coord._reconnect_task = None
    coord._idle_released = False
    coord._idle_check_running = False
    coord._firmware_pushed = True
    coord._idle_disconnect_minutes = minutes
    coord._last_active_at = time.monotonic() - idle_for
    coord.async_update_listeners = lambda: None
    spawned = []

    def create_task(coro, **kwargs):
        coro.close()
        spawned.append(kwargs.get("name"))

    coord.async_set_updated_data = lambda data: setattr(coord, "data", data)
    coord.hass = types.SimpleNamespace(async_create_task=create_task)
    return coord, pad, spawned


@pytest.mark.parametrize("belt", [const.BeltState.STOPPED, const.BeltState.PAUSED])
def test_idle_link_is_released_and_stays_down(belt):
    coord, pad, spawned = make_coordinator(belt=belt)
    asyncio.run(coord._async_update_data())
    assert not pad.connected
    coord._async_handle_disconnect()
    asyncio.run(coord._async_update_data())
    assert spawned == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minutes": 0},
        {"idle_for": 10},
        {"belt": const.BeltState.ACTIVE},
        {"belt": const.BeltState.STARTING},
    ],
)
def test_link_is_kept(kwargs):
    coord, pad, _ = make_coordinator(**kwargs)
    assert not asyncio.run(coord._async_check_idle())
    assert pad.connected


def test_recent_command_keeps_the_link():
    coord, pad, _ = make_coordinator()
    asyncio.run(pad.set_speed(2.0))
    assert not asyncio.run(coord._async_check_idle())
    assert pad.connected


def test_command_in_flight_wins():
    coord, pad, _ = make_coordinator()

    async def run():
        await pad._ble_lock.acquire()
        check = asyncio.create_task(coord._async_check_idle())
        await asyncio.sleep(0)
        pad._last_command_at = time.monotonic()
        pad._ble_lock.release()
        return await check

    assert not asyncio.run(run())
    assert pad.connected and not coord._idle_released


def test_command_after_release_rearms_reconnects():
    coord, pad, spawned = make_coordinator()
    asyncio.run(coord._async_check_idle())
    asyncio.run(pad.set_speed(2.0))  # reconnects
    assert not asyncio.run(coord._async_check_idle())
    pad._connection_status = walkingpad.WalkingPadConnectionStatus.NOT_CONNECTED
    coord._async_handle_disconnect()
    assert spawned == ["walkingpad-reconnect"]


def test_turning_the_option_off_reconnects():
    coord, _, spawned = make_coordinator()
    asyncio.run(coord._async_check_idle())
    coord.set_idle_disconnect_minutes(0)
    assert spawned == ["walkingpad-reconnect"]


def test_drop_right_after_a_command_reconnects():
    # The link drops before any poll has seen it up again.
    coord, pad, spawned = make_coordinator()
    asyncio.run(coord._async_check_idle())
    asyncio.run(pad.set_speed(2.0))
    pad._connection_status = walkingpad.WalkingPadConnectionStatus.NOT_CONNECTED
    coord._async_handle_disconnect()
    assert spawned == ["walkingpad-reconnect"]


def test_status_updates_alone_release_an_idle_link():
    # Pushed updates postpone the poll, so the check must not depend on it.
    coord, pad, _ = make_coordinator()
    tasks = []

    async def run():
        coord.hass.async_create_task = lambda coro, **kw: tasks.append(
            asyncio.ensure_future(coro)
        )
        coord._async_handle_update(
            {"belt_state": const.BeltState.STOPPED, "status_timestamp": 1}
        )
        await asyncio.gather(*tasks)

    asyncio.run(run())
    assert len(tasks) == 1 and not pad.connected


def test_walk_between_polls_restarts_the_idle_time():
    coord, pad, spawned = make_coordinator()
    for ts, belt in ((1, const.BeltState.ACTIVE), (2, const.BeltState.STOPPED)):
        coord._async_handle_update({"belt_state": belt, "status_timestamp": ts})
    assert not asyncio.run(coord._async_check_idle())
    assert pad.connected and spawned == []


def test_status_during_the_release_does_not_rearm_reconnects():
    coord, pad, spawned = make_coordinator()

    async def disconnect():
        coord._async_handle_update(
            {"belt_state": const.BeltState.STOPPED, "status_timestamp": 1}
        )
        pad._connection_status = walkingpad.WalkingPadConnectionStatus.NOT_CONNECTED
        coord._async_handle_disconnect()

    pad.disconnect = disconnect
    assert asyncio.run(coord._async_check_idle())
    coord._async_handle_disconnect()
    assert spawned == [] and coord._idle_released


def test_longer_idle_time_reconnects_a_released_link():
    coord, _, spawned = make_coordinator()
    asyncio.run(coord._async_check_idle())
    coord.set_idle_disconnect_minutes(30)
    assert spawned == ["walkingpad-reconnect"]
