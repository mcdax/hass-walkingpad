# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic
Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
