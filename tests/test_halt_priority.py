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


async def hanging_sound(pad, trace):
    """Start a sound change that holds the BLE lock until released."""
    release = asyncio.Event()

    async def set_sound(on):
        trace.append("sound")
        await release.wait()
        return True

    pad._controller.set_sound = set_sound
    task = asyncio.create_task(pad.set_sound(True))
    await asyncio.sleep(0.01)
    return release, task


@pytest.mark.parametrize("queued", ["start_belt", "set_speed"])
def test_halt_drops_a_start_queued_before_it(queued):
    trace: list[str] = []
    pad = make_pad(CONNECTED, False, trace)

    async def run():
        release, sound = await hanging_sound(pad, trace)
        args = (3.0,) if queued == "set_speed" else ()
        task = asyncio.create_task(getattr(pad, queued)(*args))
        await asyncio.sleep(0)
        await pad.stop_belt()
        release.set()
        await sound
        await task
        await pad.start_belt()  # requested after the stop: runs

    asyncio.run(run())
    assert [t for t in trace if t != "disconnect_after_command"] == [
        "sound",
        "stop",
        "start",
    ]


def test_halt_queues_behind_a_running_start():
    trace: list[str] = []
    pad = make_pad(CONNECTED, False, trace)

    async def run():
        moving = asyncio.Event()

        async def start():
            trace.append("start")
            await moving.wait()
            trace.append("moving")
            return True

        pad._controller.start = start
        start_task = asyncio.create_task(pad.start_belt())
        await asyncio.sleep(0.01)
        pause = asyncio.create_task(pad.pause_belt())
        await asyncio.sleep(0.01)
        moving.set()
        await asyncio.gather(start_task, pause)

    asyncio.run(run())
    assert [t for t in trace if t != "disconnect_after_command"] == [
        "start",
        "moving",
        "pause",
    ]


def test_no_idle_disconnect_while_a_halt_runs(monkeypatch):
    monkeypatch.setattr(walkingpad, "IDLE_DISCONNECT_TIMEOUT_SECONDS", 0.01)
    trace: list[str] = []
    pad = make_pad(CONNECTED, False, trace)
    del pad.disconnect_after_command  # the real one
    pad._stay_connected = False

    async def disconnect():
        trace.append("disconnect")

    pad.disconnect = disconnect

    async def run():
        release, sound = await hanging_sound(pad, trace)

        async def stop():
            release.set()  # the sound change ends and arms the idle timer
            await asyncio.sleep(0.05)
            trace.append("stop")
            return True

        pad._controller.stop = stop
        await pad.stop_belt()
        await sound
        assert trace == ["sound", "stop"]
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert trace == ["sound", "stop", "disconnect"]


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

    async def run():
        release, sound = await hanging_sound(pad, trace)

        async def stop():
            trace.append("stop")
            if trace.count("stop") == 1:
                pad._controller.connected = False  # no disconnect callback yet
                release.set()
                return False
            return True

        pad._controller.stop = stop
        await pad.stop_belt()
        await sound

    asyncio.run(run())
    assert [t for t in trace if t != "disconnect_after_command"] == [
        "sound",
        "stop",
        "connect",
        "stop",
    ]


def test_refused_halt_is_reported_without_a_second_try():
    trace: list[str] = []
    pad = make_pad(CONNECTED, False, trace)

    async def run():
        release, sound = await hanging_sound(pad, trace)

        async def stop():
            trace.append("stop")
            return False

        pad._controller.stop = stop
        with pytest.raises(HomeAssistantError):
            await pad.stop_belt()
        release.set()
        await sound

    asyncio.run(run())
    assert trace.count("stop") == 1
