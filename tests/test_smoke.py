"""Smoke tests: entity setup and the BLE command path, with fakes only."""

from __future__ import annotations

import asyncio
import types

import homeassistant.helpers.entity_registry as er
import pytest
from bleak.exc import BleakError

from king_smith import binary_sensor, button, const, number, sensor, switch, walkingpad

MAC = "AA:BB:CC:DD:EE:FF"
PLATFORMS = (binary_sensor, button, number, sensor, switch)


class FakeDevice:
    mac = MAC
    name = "KS-HD-Z1D"
    firmware_version = ""
    min_speed = 0.5
    max_speed = 6.0
    speed_increment = 0.1
    stay_connected = True
    supports_sound = True

    def __init__(self, protocol):
        self.protocol = protocol


class FakeCoordinator:
    last_update_success = True

    def __init__(self, protocol):
        self.walkingpad_device = FakeDevice(protocol)
        self.connected = True
        self.data = {"belt_state": const.BeltState.STOPPED, "status_timestamp": 0}

    def async_add_listener(self, update_callback, context=None):
        return lambda: None


class FakeRegistry:
    def __init__(self):
        self.removed: list[str] = []

    def async_get_entity_id(self, platform, domain, unique_id):
        return f"{platform}.{unique_id}"

    def async_remove(self, entity_id):
        self.removed.append(entity_id)


def setup_entities(monkeypatch, protocol, options):
    registry = FakeRegistry()
    monkeypatch.setattr(er, "async_get", lambda hass: registry)
    coordinator = FakeCoordinator(protocol)
    hass = types.SimpleNamespace(
        data={
            const.DOMAIN: {
                "entry": {"coordinator": coordinator, "device": coordinator.walkingpad_device}
            }
        }
    )
    entry = types.SimpleNamespace(entry_id="entry", options=options, data={"mac": MAC})
    entities = []

    async def run():
        for platform in PLATFORMS:
            await platform.async_setup_entry(hass, entry, entities.extend)

    asyncio.run(run())
    return entities, registry.removed


@pytest.mark.parametrize(
    "protocol", [const.ProtocolType.FTMS, const.ProtocolType.WILINK, const.ProtocolType.SPERAX]
)
@pytest.mark.parametrize(
    "options",
    [
        {"remote_control_enabled": True, "preferred_mode": "manual"},
        {"remote_control_enabled": True, "preferred_mode": "auto"},
        {"remote_control_enabled": False},
    ],
)
def test_entities_have_stable_ids_and_one_device(monkeypatch, protocol, options):
    entities, _ = setup_entities(monkeypatch, protocol, options)
    unique_ids = [e.unique_id for e in entities]
    assert len(unique_ids) == len(set(unique_ids))
    for entity in entities:
        assert entity.unique_id.startswith(f"{MAC}-walkingpad_")
        assert entity.device_info["identifiers"] == {(const.DOMAIN, MAC)}
        assert entity.has_entity_name


def test_remote_control_off_removes_control_entities(monkeypatch):
    entities, removed = setup_entities(
        monkeypatch, const.ProtocolType.FTMS, {"remote_control_enabled": False}
    )
    assert f"button.{MAC}-walkingpad_stop_session" in removed
    assert f"switch.{MAC}-walkingpad_belt_switch" in removed
    assert f"number.{MAC}-walkingpad_speed" in removed
    assert not any(e.unique_id.endswith("walkingpad_belt_switch") for e in entities)


def test_sperax_numbers_keep_their_manufacturer(monkeypatch):
    entities, _ = setup_entities(
        monkeypatch,
        const.ProtocolType.SPERAX,
        {"remote_control_enabled": True, "preferred_mode": "manual"},
    )
    manufacturers = {
        e.unique_id.rsplit("-", 1)[1]: e.device_info["manufacturer"] for e in entities
    }
    assert manufacturers["walkingpad_incline"] == "Sperax"
    assert manufacturers["walkingpad_speed"] == "KingSmith"


class FakeController:
    def __init__(self, trace, fail):
        self.trace = trace
        self.fail = fail
        self.protocol = const.ProtocolType.FTMS
        self.connected = True

    def __getattr__(self, name):
        async def command(*args):
            self.trace.append(name)
            if self.fail:
                raise BleakError("boom")
            return True

        return command


def make_pad(status, fail, trace):
    pad = walkingpad.WalkingPad.__new__(walkingpad.WalkingPad)
    pad._controller = FakeController(trace, fail)
    pad._callbacks = []
    pad._disconnect_callbacks = []
    pad._connection_status = status
    pad._ble_lock = asyncio.Lock()
    pad._stay_connected = True
    pad._disconnect_timer_task = None
    pad._connect_done = asyncio.Event()
    pad._connect_done.set()
    pad._closed = False
    pad._idle_generation = 0
    pad._halt_generation = 0
    pad._last_command_at = 0.0

    async def connect():
        trace.append("connect")
        pad._connection_status = walkingpad.WalkingPadConnectionStatus.CONNECTED

    async def disconnect_after_command():
        trace.append("disconnect_after_command")

    pad.connect = connect
    pad.disconnect_after_command = disconnect_after_command
    return pad


@pytest.mark.parametrize("fail", [False, True])
def test_command_connects_runs_and_schedules_idle_disconnect(fail):
    trace: list[str] = []
    pad = make_pad(walkingpad.WalkingPadConnectionStatus.NOT_CONNECTED, fail, trace)
    asyncio.run(pad.set_speed(3.0))
    assert trace == ["connect", "set_speed", "disconnect_after_command"]
    expected = (
        walkingpad.WalkingPadConnectionStatus.NOT_CONNECTED
        if fail
        else walkingpad.WalkingPadConnectionStatus.CONNECTED
    )
    assert pad._connection_status == expected


def test_stop_during_a_reconnect_runs_once_the_connect_finishes():
    # Was: silently dropped while the reconnect loop was CONNECTING.
    trace: list[str] = []
    pad = make_pad(walkingpad.WalkingPadConnectionStatus.CONNECTING, False, trace)

    async def run():
        pad._connect_done.clear()

        async def finish_reconnect():
            await asyncio.sleep(0.01)
            pad._connection_status = walkingpad.WalkingPadConnectionStatus.CONNECTED
            pad._connect_done.set()

        asyncio.get_running_loop().create_task(finish_reconnect())
        await pad.stop_belt()

    asyncio.run(run())
    assert trace == ["stop", "disconnect_after_command"]


def test_stop_reports_an_unreachable_device(monkeypatch):
    from homeassistant.exceptions import HomeAssistantError

    trace: list[str] = []
    pad = make_pad(walkingpad.WalkingPadConnectionStatus.NOT_CONNECTED, False, trace)

    async def connect_fails():
        trace.append("connect")

    pad.connect = connect_fails
    with pytest.raises(HomeAssistantError):
        asyncio.run(pad.stop_belt())
    assert trace == ["connect"]


@pytest.mark.parametrize("fail", [False, True])
def test_stop_reports_a_failed_or_rejected_command(fail):
    from homeassistant.exceptions import HomeAssistantError

    trace: list[str] = []
    pad = make_pad(walkingpad.WalkingPadConnectionStatus.CONNECTED, fail, trace)
    if not fail:
        async def rejected():
            trace.append("stop")
            return False

        pad._controller.stop = rejected
    with pytest.raises(HomeAssistantError):
        asyncio.run(pad.stop_belt())
    assert "disconnect_after_command" in trace


def test_speed_change_stays_quiet_when_unreachable():
    trace: list[str] = []
    pad = make_pad(walkingpad.WalkingPadConnectionStatus.NOT_CONNECTED, False, trace)

    async def connect_fails():
        trace.append("connect")

    pad.connect = connect_fails
    asyncio.run(pad.set_speed(2.0))  # no exception, unchanged behaviour


def test_belt_controls_stay_available_while_disconnected(monkeypatch):
    # With Stay-connected off the link drops while the belt may still run;
    # Stop must stay usable then, like the belt switch and the slider.
    entities, _ = setup_entities(
        monkeypatch,
        const.ProtocolType.FTMS,
        {"remote_control_enabled": True, "preferred_mode": "manual"},
    )
    by_key = {e.unique_id.split("-", 1)[1]: e for e in entities}
    for entity in by_key.values():
        entity.coordinator.connected = False
    for key in ("walkingpad_stop_session", "walkingpad_belt_switch", "walkingpad_speed"):
        assert by_key[key].available, key


def test_no_commands_after_close():
    from homeassistant.exceptions import HomeAssistantError

    trace: list[str] = []
    pad = make_pad(walkingpad.WalkingPadConnectionStatus.NOT_CONNECTED, False, trace)
    pad._closed = True
    with pytest.raises(HomeAssistantError):
        asyncio.run(pad.stop_belt())
    asyncio.run(pad.set_speed(2.0))
    assert trace == []  # neither connected nor sent


def test_stale_idle_timer_does_not_cut_a_fresh_command(monkeypatch):
    # The timer fires while a command holds the BLE lock; the command arms
    # a new timer, so the old one must not disconnect when it gets the lock.
    monkeypatch.setattr(walkingpad, "IDLE_DISCONNECT_TIMEOUT_SECONDS", 0.01)
    trace: list[str] = []
    pad = make_pad(walkingpad.WalkingPadConnectionStatus.CONNECTED, False, trace)
    pad._stay_connected = False

    async def disconnect():
        trace.append("disconnect")

    pad.disconnect = disconnect

    async def run():
        await pad._ble_lock.acquire()
        pad._reschedule_idle_disconnect()
        old = pad._disconnect_timer_task
        await asyncio.sleep(0.03)  # old timer fired, waits for the lock
        pad._reschedule_idle_disconnect()  # the command's new timer
        pad._ble_lock.release()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "disconnect" not in trace  # old timer gave up
        assert old.done()
        await asyncio.sleep(0.05)  # new timer fires normally

    asyncio.run(run())
    assert trace == ["disconnect"]


def test_close_during_connect_leaves_no_link():
    events: list[str] = []
    gate = None

    class Controller:
        protocol = const.ProtocolType.FTMS

        async def connect(self):
            events.append("connect")
            await gate.wait()

        async def disconnect(self):
            events.append("disconnect")

    pad = make_pad(walkingpad.WalkingPadConnectionStatus.NOT_CONNECTED, False, [])
    del pad.connect  # use the real WalkingPad.connect
    pad._controller = Controller()

    async def run():
        nonlocal gate
        gate = asyncio.Event()
        task = asyncio.create_task(pad.connect())
        await asyncio.sleep(0)
        pad.close()
        gate.set()
        await task

    asyncio.run(run())
    assert events == ["connect", "disconnect"]
    assert not pad.connected


def test_no_idle_timer_after_close():
    pad = make_pad(walkingpad.WalkingPadConnectionStatus.CONNECTED, False, [])
    del pad.disconnect_after_command  # the real one
    pad._stay_connected = False
    pad.close()

    async def run():
        await pad.disconnect_after_command()

    asyncio.run(run())
    assert pad._disconnect_timer_task is None


def test_failed_sound_change_keeps_the_state():
    from homeassistant.exceptions import HomeAssistantError

    coordinator = FakeCoordinator(const.ProtocolType.FTMS)
    entity = switch.WalkingPadSoundSwitch(coordinator)
    written = []
    entity.async_write_ha_state = lambda: written.append(entity.is_on)

    async def ok(on):
        pass

    async def fails(on):
        raise HomeAssistantError("no")

    coordinator.walkingpad_device.set_sound = ok
    asyncio.run(entity.async_turn_off())
    coordinator.walkingpad_device.set_sound = fails
    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_turn_on())
    assert written == [False] and entity.is_on is False
