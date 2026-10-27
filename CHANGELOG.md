# Changelog

All notable changes to RazerTux. Each version below becomes a GitHub release with these notes.

## [0.4.1] - 2026-10-27

Fixes from the first real device recording (Naga V2 Pro).

### Fixed
- Device recorder: a button that was pressed once before (for example the left button when
  clicking "Start") can now be recorded in a later step. Buttons no longer need several presses.
- Device recorder: "Not on this device" no longer appears where "Start" was, so the next click
  cannot mark the left button as missing by accident.
- Device recorder: DPI stages are now included in the report.
- Device recorder: when a report is too long for the issue link, the file is saved
  automatically and the issue form says which file to attach.

### Changed
- Device recorder: the button step now asks to wait a moment and to press again if nothing
  happens.

## [0.4.0] - 2026-10-27

### Added
- Device recorder ("Help → Record device…", tray menu, `razertux-record`): a guided wizard for
  any Razer device (mice, keyboards, headsets, mouse pads, docks, keypads). It records device
  data, buttons, read-only values and an optional light test, and opens a prefilled GitHub issue.
  Access to the device is granted per recording with a password prompt (polkit); no permanent
  rights, no keylogging (only which signals arrive, never order or typed text), no serial
  numbers or personal data in the report.
- Device database for all Razer device kinds (recognition only).
- Import tool for device reports (`tools/import_device_report.py`, for maintainers).
- The service pauses its button mapping while a recording runs and resumes on its own.

### Changed
- English is the source language of the whole project; German, Spanish and French are
  translations.

## [0.3.0] - 2026-10-23

### Added
- Support for all Razer mice known to OpenRazer (experimental: read-only until a safe check
  passes).
- One session per connected mouse; device selector in the window.
- Green "laser" drawings of the mice, including all three Naga side plates, and templates for
  common mouse shapes.
- "Learn a button" for mice with unknown buttons.
- On-screen display when the profile or DPI changes.
- Scroll options and lighting zones per device.

### Fixed
- Clean shutdown and upgrade hints when coming from 0.2.x.

## [0.2.3] - 2026-10-23

### Added
- Adjustable upper limit of the DPI slider (default 10000).
- Only one window per user: starting RazerTux again shows the running window.

### Fixed
- The tray menu stayed empty after the service restarted.

## [0.2.2] - 2026-10-23

### Fixed
- DPI stages use the mouse's onboard table, so the order and the active stage are right.

## [0.2.1] - 2026-10-23

### Fixed
- An invalid DPI stage reported by the mouse (very high DPI after pressing the DPI button) is
  corrected and limited to what the mouse supports.

## [0.2.0] - 2026-10-22

### Changed
- New name: RazerTux. The settings move from `~/.config/naga-config` automatically.

### Added
- English, Spanish and French translations with a language switch.
- Synapse-like customize page, app icon and About dialog.

## [0.1.0] - 2026-10-22

First version for the Razer Naga V2 Pro.

### Added
- Button remapping with profiles, HyperShift and macros (with a macro recorder).
- DPI, polling rate, lighting, power and battery settings; scroll wheel modes.
- Background service with tray icon; the mouse's own stored settings stay untouched.
- Arch Linux package.
