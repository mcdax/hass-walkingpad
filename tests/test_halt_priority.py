"""Stop and pause overtake other commands."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError

from king_smith import const, walkingpad
from test_smoke import make_pad

CONNECTED = walkingpad.WalkingPadConnectionStatus.CONNECTED
NOT_CONNECTED = walkingpad.WalkingPadConnectionStatus.NOT_CONNECTED


@pytest.mark.parametrize("halt", ["stop", "pause"])
def test_halt_overtakes_a_hanging_sound_change(halt):
    trace: list[str] = []
    pad = make_pad(CONNECTED, False, trace)

    async def run():
        release = asyncio.Event()

        async def set_sound(on):
            trace.append("sound")
            await release.wait()
            return True

        pad._controller.set_sound = set_sound
        sound = asyncio.create_task(pad.set_sound(True))
        await asyncio.sleep(0)
        await asyncio.wait_for(getattr(pad, f"{halt}_belt")(), 0.2)
        assert trace[:2] == ["sound", halt]
        release.set()
        await sound

    asyncio.run(run())


@pytest.mark.parametrize("queued", ["start_belt", "set_speed"])
def test_halt_drops_a_start_queued_before_it(queued):
    trace: list[str] = []
    pad = make_pad(CONNECTED, False, trace)

    async def run():
        await pad._ble_lock.acquire()
        args = (3.0,) if queued == "set_speed" else ()
        task = asyncio.create_task(getattr(pad, queued)(*args))
        await asyncio.sleep(0)
        await pad.stop_belt()
        pad._ble_lock.release()
        await task
        await pad.start_belt()  # requested after the stop: runs

    asyncio.run(run())
    assert [t for t in trace if t != "disconnect_after_command"] == ["stop", "start"]


def test_halt_during_the_mode_switch_prevents_the_start(monkeypatch):
    monkeypatch.setattr(walkingpad, "MODE_SWITCH_SETTLE_SECONDS", 0.05)
    trace: list[str] = []
    pad = make_pad(CONNECTED, False, trace)

    async def run():
        task = asyncio.create_task(pad.start_belt_in_mode(const.WalkingPadMode.MANUAL))
        await asyncio.sleep(0.01)
        await pad.pause_belt()
        await task

    asyncio.run(run())
    assert "pause" in trace and "start" not in trace


def test_halt_on_a_dropped_link_reconnects():
    trace: list[str] = []
    pad = make_pad(CONNECTED, False, trace)

    async def stop():
        trace.append("stop")
        if trace.count("stop") == 1:
            pad._connection_status = NOT_CONNECTED
            return False
        return True

    pad._controller.stop = stop
    asyncio.run(pad.stop_belt())
    assert trace == ["stop", "connect", "stop", "disconnect_after_command"]


def test_refused_halt_is_reported_without_a_second_try():
    trace: list[str] = []
    pad = make_pad(CONNECTED, False, trace)

    async def stop():
        trace.append("stop")
        return False

    pad._controller.stop = stop
    with pytest.raises(HomeAssistantError):
        asyncio.run(pad.stop_belt())
    assert trace.count("stop") == 1


def test_other_protocols_keep_the_command_order():
    trace: list[str] = []
    pad = make_pad(CONNECTED, False, trace)
    pad._controller.protocol = const.ProtocolType.WILINK

    async def run():
        await pad._ble_lock.acquire()
        task = asyncio.create_task(pad.stop_belt())
        await asyncio.sleep(0.01)
        assert "stop" not in trace
        pad._ble_lock.release()
        await task

    asyncio.run(run())
    assert "stop" in trace
