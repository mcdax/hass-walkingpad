"""Fixes from the review of the BLE-name / device-lookup changes."""

from __future__ import annotations

import asyncio
import types

import king_smith
from king_smith import const, coordinator as coordinator_mod, walkingpad


class FakeConfigEntries:
    def __init__(self):
        self.unloaded: list = []
        self.set_up: list = []
        self.unload_ok = True

    async def async_unload_platforms(self, entry, platforms):
        self.unloaded.append(list(platforms))
        return self.unload_ok

    async def async_forward_entry_setups(self, entry, platforms):
        self.set_up.append(list(platforms))


def _hass_and_entry(options, stored_options):
    entries = FakeConfigEntries()
    entry = types.SimpleNamespace(entry_id="e1", options=options, data={"mac": "AA"})
    hass = types.SimpleNamespace(
        config_entries=entries,
        data={const.DOMAIN: {"e1": {"options": dict(stored_options)}}},
    )
    return hass, entry, entries


def test_data_only_update_does_not_reload_the_controls():
    # Storing the learned BLE name updates entry.data and fires the listener.
    hass, entry, entries = _hass_and_entry({"remote_control_enabled": True}, {"remote_control_enabled": True})
    asyncio.run(king_smith.async_update_options(hass, entry))
    assert entries.unloaded == [] and entries.set_up == []


def test_options_change_reloads_the_controls_once():
    hass, entry, entries = _hass_and_entry({"remote_control_enabled": False}, {"remote_control_enabled": True})
    asyncio.run(king_smith.async_update_options(hass, entry))
    assert len(entries.unloaded) == 1 and len(entries.set_up) == 1
    asyncio.run(king_smith.async_update_options(hass, entry))
    assert len(entries.set_up) == 1  # unchanged since


def test_failed_unload_skips_the_setup():
    hass, entry, entries = _hass_and_entry({"remote_control_enabled": False}, {"remote_control_enabled": True})
    entries.unload_ok = False
    asyncio.run(king_smith.async_update_options(hass, entry))
    assert entries.set_up == []
    entries.unload_ok = True  # the next update retries the reload
    asyncio.run(king_smith.async_update_options(hass, entry))
    assert len(entries.set_up) == 1


class NewRegistry:
    def __init__(self):
        self.calls = []

    def async_get_device_by_identifier(self, identifier, config_entry_id):
        self.calls.append((identifier, config_entry_id))
        return "device"

    def async_get_device(self, identifiers):  # pragma: no cover - must not be used
        raise AssertionError("deprecated lookup used")


class OldRegistry:
    def async_get_device(self, identifiers):
        return ("old", identifiers)


def test_device_lookup_uses_the_entry_scoped_api():
    reg = NewRegistry()
    assert coordinator_mod._device_by_identifier(reg, ("king_smith", "AA"), "e1") == "device"
    assert reg.calls == [(("king_smith", "AA"), "e1")]


def test_device_lookup_falls_back_on_old_cores():
    assert coordinator_mod._device_by_identifier(OldRegistry(), ("king_smith", "AA"), "e1") == (
        "old",
        {("king_smith", "AA")},
    )


def test_no_reconnect_after_shutdown_started():
    coord = object.__new__(coordinator_mod.WalkingPadCoordinator)
    coord._shutting_down = True
    coord._reconnect_task = None
    coord.walkingpad_device = types.SimpleNamespace(stay_connected=True)
    coord.hass = types.SimpleNamespace(async_create_task=lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))
    coord._ensure_reconnect_running()
    assert coord._reconnect_task is None


def test_learned_name_reaches_the_controller():
    names = []
    wp = object.__new__(walkingpad.WalkingPad)
    wp._controller = types.SimpleNamespace(set_name=names.append)
    wp.set_ble_name("KS-HD-Z1D")
    assert names == ["KS-HD-Z1D"]


def test_learned_name_is_ignored_by_an_older_library():
    wp = object.__new__(walkingpad.WalkingPad)
    wp._controller = types.SimpleNamespace()
    wp.set_ble_name("KS-HD-Z1D")  # no AttributeError
