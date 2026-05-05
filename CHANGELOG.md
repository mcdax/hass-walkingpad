# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic
Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.8] - 2026-05-05

### Fixed

- **Speed slider now works while disconnected.** With Stay-connected
  off and the belt stopped, dragging the slider previously did nothing
  — HA gated the service call on the entity's "available" flag, which
  was tied to the BLE link. The slider now stays available, the
  underlying library connects-and-issues, and the belt starts at the
  requested speed. (Bug #9 in the audit.)
- **Belt switch entity_id is no longer mode-locked.** Earlier, the
  manual-mode and auto-mode switch classes shared one `unique_id` but
  used different `translation_key`s, so the entity_id froze to
  whichever class was first registered. Both variants now use the
  unified `walkingpad_belt` translation_key; existing installs are
  migrated. (Bug #2.)
- **Belt switch goes unavailable when disconnected.** Matches the
  sensor behavior; previously the switch silently no-op'd if you
  tapped it while the link was down. (Bug #11.)
- **Stale 2ADA event on every reconnect.** Fixed in
  walkingpad-controller 0.4.5 — KingSmith firmware replays its
  current Fitness Machine Status when the CCCD is enabled, which
  flipped `last_fm_event` to a misleading historical value within a
  second of every connect. The first 2ADA event after subscribe is
  now skipped. (Bug #5.)

### Changed

- **Canonical English entity_ids on every install.** New entities now
  pin their entity_id to a stable English slug derived from the
  unique_id key, rather than letting HA derive it from the localized
  friendly_name. Existing installs are migrated on integration
  reload — entities like `sensor.state`, `switch.stay_connected`,
  `number.speed`, and `sensor.walkingpad_kalorienrate` are renamed to
  `sensor.walkingpad_state`, `switch.walkingpad_stay_connected`,
  `number.walkingpad_speed`, `sensor.walkingpad_calories_per_hour`,
  and so on.

  **Action required:** any automations, scripts, or dashboards
  referencing the old entity_ids need to be updated. The migration
  log will list each rename in the HA logs at integration reload, so
  you can grep for "Migrating entity_id" to see exactly what changed.
- **Enum sensor states are now translated.** `Mode`, `State`,
  `Training status`, `Last FTMS event`, and `Protocol` sensors now
  show user-readable values in the UI's locale (English/German)
  instead of raw snake_case identifiers. (Bug #4 / #8.)
- Bumps `walkingpad-controller` to **0.4.5**.

### Known limitations

- On KS-HD-Z1D (and likely other KS-HD-* models), the `Last FTMS
  event` sensor only captures **start** and **stop** events. The
  device's firmware does not emit `target_speed_changed` 2ADA events
  when speed is changed via the Control Point — the new speed shows
  up only via the live `Treadmill Data` notifications, which drive
  `Current speed` directly. (Bug #6.)
- Rapid switch toggles within roughly one second (e.g. tapping the
  Belt switch off+on in quick succession) may lose the second
  command. Wait for the device to reach the first state before
  issuing the next. (Bug #10 — work in progress.)

## [0.4.7] - 2026-05-05

### Added

- **Connected** binary sensor (`binary_sensor.walkingpad_connected`,
  `device_class=connectivity`, diagnostic) showing whether HA currently
  has an active BLE link to the treadmill.
- **Calorie rate** sensor (kcal/h, FTMS only).
- **Heart rate** sensor (bpm, FTMS only). Reports as unavailable when
  no HR strap is paired with the treadmill — FTMS reports `0` in that
  case, which would otherwise mislead.
- **Protocol** sensor (enum: `ftms` / `wilink` / `unknown`, diagnostic).
- **Min speed**, **Max speed**, **Speed increment** sensors (km/h,
  diagnostic) — exposes the device-capability values that already drive
  the speed slider, so they're available for use in templates and
  automations.
- **Firmware version** sensor (string, FTMS-only, diagnostic) —
  populated from the FTMS Software Revision String. Also continues to
  appear as `sw_version` on the device card.

### Fixed

- The library's BLE-disconnect callback was previously not propagated
  to the coordinator, meaning entities (sensor `available`, switches
  `is_on`, the new connected binary sensor) didn't react to mid-session
  link drops promptly. The disconnect now flows through to all
  registered listeners.

### Changed

- Internal: `WalkingPadSensorEntityDescription.value_fn` now receives
  the coordinator instead of the bare status dict, so static device-
  info sensors and dynamic status sensors share the same description
  class. New `static=True` flag marks sensors whose value comes from
  device capabilities and stays available even with BLE down.

## [0.4.6] - 2026-05-05

### Changed

- When **Stay connected** is OFF, the BLE link is now held for **5 seconds
  after the last action** before being dropped, instead of disconnecting
  immediately after every command. This prevents connect/disconnect churn
  when issuing a burst of actions in quick succession (e.g. start belt
  then set speed, or set speed multiple times). Each new action resets
  the 5 s timer; the link is dropped once the timer expires with no new
  activity.

## [0.4.5] - 2026-05-05

### Changed

- The **Stay connected** toggle is now strictly user-controlled. Pressing
  the **Belt** switch (in either Manual or Auto mode) no longer flips
  Stay-connected on or off as a side effect; whatever value the user has
  set is preserved across belt start/stop. Removes the deferred-disconnect
  plumbing that was used to "borrow and return" the toggle for the
  duration of a walk.

  If you have Stay-connected **off** and you start the belt, the library
  will connect just long enough to send the start command and disconnect
  immediately afterwards — the belt keeps running, but live sensors stay
  stale until you re-enable Stay-connected.

## [0.4.4] - 2026-05-04

### Added

- Two new diagnostic sensors on FTMS devices, populated from the Training
  Status (`0x2AD3`) and Fitness Machine Status (`0x2ADA`) notifications
  the library subscribes to:
  - **Training status** — Bluetooth SIG FTMS standard enum: `idle`,
    `warming_up`, `low_intensity_interval`, `high_intensity_interval`,
    `recovery_interval`, `cool_down`, `manual_mode`, `pre_workout`,
    `post_workout`, etc.
  - **Last FTMS event** — opcode of the most recent state-change event:
    `started_or_resumed`, `stopped_or_paused`, `target_speed_changed`,
    `target_inclination_changed`, etc.

### Changed

- Bumps `walkingpad-controller` to **0.4.4**, which extends
  `TreadmillStatus` with `training_status` and `last_fm_event` fields
  and fires the status callback whenever they change.

## [0.4.3] - 2026-05-04

### Added

- device card now shows the **firmware version** (read from FTMS Software
  Revision String 0x2A28) and the BLE-advertised model name. Powered by
  the new properties in `walkingpad-controller` 0.4.3.

### Changed

- bumps `walkingpad-controller` to **0.4.3**, which (a) adds eager FTMS
  detection for the MC-21 family (`KS-MC21-*`, `KS-SMC21C-*`,
  `ZP-ZEALR1-*`), (b) staggers the FTMS CCCD subscriptions to match KS
  Fit's connect timing, (c) acknowledges Control Point commands via
  Fitness Machine Status events on the vendor pre-amble path, and
  (d) subscribes to Training Status (`0x2AD3`).

## [0.4.2] - 2026-05-04

### Fixed

- pressing the belt **Stop** switch no longer drops the BLE connection
  and silently flips the **Stay connected** toggle off when the user
  has opted in to a persistent connection. The deferred-disconnect now
  only fires when the belt switch had to flip `stay_connected` on for
  the duration of the walk (i.e. it was off beforehand).

## [0.4.1] - 2026-05-04

### Fixed

- speed control on KingSmith MC-21 (and other models exposing the
  vendor pre-amble characteristic) — bumps `walkingpad-controller`
  to 0.4.1, which writes the required pre-amble before each FTMS
  Control Point command and tolerates `REQUEST_CONTROL` rejection.
  See [walkingpad-controller#1](https://github.com/mcdax/walkingpad-controller/issues/1).

## [0.3.0] - 2025-11-15

### Added

- remote control configuration options
- new sensors (walkingpad_state and walkingpad_mode)
- belt control switch
- speed control

## [0.2.1] - 2025-08-08

### Changed

- upgrade homeassistant to 2025.8.0
- upgrade ph4-walkingpad to 1.0.2

### Fixed

- error when using this integration with latest versions of bleak

## [0.2.0] - 2025-02-23

### Changed

- add support for more WalkingPad models
- upgrade homeassistant to 2025.2.5

### Fixed

- warnings about invalid suggested_unit_of_measurement
- add missing translations for sensor names
- handle unknown belt states

## [0.1.0] - 2024-03-29

### Added

- support for WalkingPad A1 Pro
- manual config flow
- bluetooth discovery config flow
- distance sensor
- steps sensor
- duration sensors (available in several units : minutes, hours, days)
- current speed sensor
- basic configuration guide in the README

[unreleased]: https://github.com/madmatah/compare/v0.3.0...main
[0.3.0]: https://github.com/madmatah/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/madmatah/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/madmatah/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/madmatah/hass-walkingpad/compare/eb2749688ebbf334fa29c5004511e8ee8680307f...v0.1.0
