# Changelog

All notable changes to RazerTux. Each version below becomes a GitHub release with these notes.

## [Unreleased]

### Added
- Razer Naga V3 Pro (1532:00E7 wired, 00E8 dongle) on the Naga V2 Pro driver. Checked
  over USB; the dongle PID is untested. Not in OpenRazer yet, so the generator adds the
  PIDs (`EXTRA_PIDS` in `tools/import_openrazer.py`). See docs/hardware-notes.md.
  - All five top buttons are freely assignable: the two DPI buttons top left (default:
    DPI stage up/down, as on the mouse), the two behind the wheel (top button
    front/rear, defaults as on the V2 Pro) and the new ring finger button (no default).
  - Own top drawing (`naga_v3_pro.json`, `naga_v3_pro_top.svg`), chosen when a V3 Pro
    is connected; a V2 Pro keeps its drawing.
  - Lighting: the scroll wheel zone (LED 0x01), which the V2 Pro does not have.
  - Shown as "Razer Naga V3 Pro" (own name per PID); a V2 Pro keeps its name, and the
    V3 Pro's DPI stage layout (x repeated in each entry) is only written to a V3 Pro.

## [0.12.2] - 2026-11-06

### Fixed
- A wireless mouse that does not answer yet when the service starts (cold boot: not
  linked to the receiver) is no longer write-protected until replugged. RazerTux tries
  again after 2, 5, 10, 20, 30 seconds and then every minute, and connects it fully
  (profile, DPI, battery) as soon as it answers (issue #4).

## [0.12.1] - 2026-11-05

### Fixed
- The window no longer crashes at start with a mouse without lighting, such as the
  non-RGB Naga V2 HyperSpeed (issue #3).
- New test: every one of the 237 devices in the database opens in the window with a full
  profile, and every keyboard opens its page, so a missing function cannot crash a tab.

## [0.12.0] - 2026-11-05

### Added
- Own drawings for the DeathAdder (classic, V2 with DPI keys, V3), Basilisk, Viper, Cobra
  and Naga Pro in the Buttons tab, instead of a generic shape. Other models of the same
  series use their family's drawing. Key positions follow product photos; please report
  a drawing that does not match your mouse.

## [0.11.0] - 2026-11-04

### Added
- Update notice: at start and once a day RazerTux asks GitHub for the newest release. A
  newer one shows a tray message (once per version) and "Update available" in the tray
  menu, which opens the update command to copy and a link to what is new. Tray menu:
  "Check for updates" switches it off.

## [0.10.0] - 2026-11-04

### Added
- Battery icon in the tray for every connected device with a battery: level as a number,
  green / orange from 20 % / red from 10 %, a bolt while charging. The tooltip names the
  device. Tray menu: "Battery icon" switches it off.
- Battery warnings as on-screen display at 20, 15, 10 and 5 % (not while charging), with
  the device name. Power tab: "Battery warnings on screen".
- The device recorder asks whether the device has a battery; the device report form has
  a "Battery" field.

### Changed
- The battery level is also read on the cable (shown with the charging bolt).
- The one-time desktop notification at 15 % is replaced by the on-screen warnings.

## [0.9.2] - 2026-11-04

### Fixed
- With OpenRazer's driver loaded, the front top button of the Naga V2 Pro also typed a
  "+" (and the rear one an arrow-up key) besides its RazerTux action.
- A side key no longer stays pressed system-wide when the Naga's magnetic side plate
  loses contact while the key is held: RazerTux releases held side buttons when the mouse
  reports the plate gone. (If side keys only work sometimes, clean the plate's contacts.)
- No more "Bad file descriptor" tracebacks in the log when the service stops (0.9.0).

## [0.9.1] - 2026-11-04

### Fixed
- Buttons did nothing (and a held side-button key could get stuck system-wide) when
  the mouse was plugged in for the first time after boot with OpenRazer installed:
  OpenRazer's driver rebinds the mouse, the new input nodes reuse the old names, and
  the late removal of the old nodes made RazerTux let go of the new ones.

## [0.9.0] - 2026-11-04

### Changed
- D-Bus library switched from `dbus-next` to its maintained fork `dbus-fast`
  (Arch: `python-dbus-fast`). `dbus-next` no longer loads on Python 3.15, so the
  service would not have started after the Python 3.15 update.

## [0.8.4] - 2026-11-04

### Changed
- Shorter README: installation and "Your mouse or keyboard is missing?" come first,
  supported distributions and devices are tables, details are collapsible.

## [0.8.3] - 2026-11-04

### Fixed
- `install.sh` on Debian and Ubuntu also installs the libraries the PySide6 from PyPI needs
  (`libgl1`, `libfontconfig1`, `libdbus-1-3`, `libgssapi-krb5-2`); without them RazerTux
  did not start on minimal Ubuntu 24.04 systems.
- `install.sh` checks that Qt's network and SVG modules load, not only the widgets.

### Changed
- The installer now runs through on Debian 13, Ubuntu 24.04, Fedora and openSUSE Tumbleweed
  (tested in containers); README and installer messages say so.

## [0.8.2] - 2026-11-04

### Fixed
- `install.sh` no longer warns that OpenRazer interferes with RazerTux; both work together
  since 0.8.0.

## [0.8.1] - 2026-11-04

### Added
- One-line install without downloading the source first:
  `curl -fsSL https://raw.githubusercontent.com/anoraknophobia/RazerTux/main/install.sh | bash`.
  The script fetches the newest release and runs its installer; `bash -s -- --uninstall`
  removes RazerTux the same way.

## [0.8.0] - 2026-10-27

### Added
- View → Device access: "RazerTux" (as before) or "RazerTux + OpenRazer". In the second mode
  everything the OpenRazer daemon offers for the mouse (lighting zones, polling rate,
  battery, power settings, DPI) goes through OpenRazer; button remapping, macros, profiles
  and the Naga's scroll-wheel stages stay with RazerTux. If OpenRazer stops, RazerTux takes
  over until it is back.

### Changed
- RazerTux can be installed next to OpenRazer (no more package conflict; OpenRazer is an
  optional dependency). The "uninstall OpenRazer" banner is gone.

### Fixed
- Naga V2 Pro top buttons in driver mode work while OpenRazer's kernel driver is loaded
  (it rewrites their reports).

## [0.7.2] - 2026-10-27

### Changed
- "Add unknown device" is now a filled Razer-green button with a plus sign, so it stands out.

## [0.7.1] - 2026-10-27

### Added
- "Add unknown device" button at the top right of the window: opens the device recorder
  (also still in Help → Record device…).

## [0.7.0] - 2026-10-27

### Added
- Razer keyboards (untested): a connected keyboard from the OpenRazer database appears in
  the device bar. Its page shows the name, the USB ID, a laser picture of a typical keyboard
  of its size (full size, tenkeyless, 75 % or 60 %) and "Record this keyboard …", which opens
  the device recorder with the keyboard already selected. Keyboards cannot be configured
  yet, and RazerTux never reads what you type.

## [0.6.0] - 2026-10-27

### Added
- Scroll wheel tab (Naga V2 Pro): set tension (0–100) and steps (8–96) of the "Custom"
  stage with two sliders and "Apply to the mouse". Rebuilt from USB captures of Synapse and
  tested on the mouse.

## [0.5.1] - 2026-10-27

### Fixed
- `install.sh` on Arch: running it again now installs the freshly built package even when
  that version is already installed (before, pacman skipped it).

## [0.5.0] - 2026-10-27

### Changed
- Naga V2 Pro top buttons: RazerTux now handles them itself, as Synapse does. Both are
  always freely assignable; the switch "Assign top buttons freely" is gone. By default the
  front button steps the scroll wheel stage and the rear one the DPI stage.
- Profiles from older versions with the switch off get these defaults for the two top
  buttons (their old assignments had no effect).

### Added
- New actions for any button: scroll wheel stage up, down and cycle (Naga V2 Pro).
- The "Custom" scroll wheel stage can be chosen by click as well. It uses the values stored
  in the mouse; changing them is only possible in Synapse so far.

### Fixed
- The front top button did nothing after the mouse had been used with Synapse.

## [0.4.4] - 2026-10-27

### Added
- Scroll wheel tab (Naga V2 Pro): click a stage to switch the mouse to it. The command was
  found in a USB capture of Synapse. "Custom" cannot be chosen yet.
- The active scroll wheel stage is read from the mouse when it connects, so it is no longer
  "unknown" until the first switch.

## [0.4.3] - 2026-10-27

### Changed
- Scroll wheel tab: the six stages have fixed names (as in Synapse) and can no longer be
  renamed. The tab explains that a stage cannot be chosen there yet, because the command for
  it is not known; switch stages with the front top button.
- Scroll wheel tab: the "Custom" stage is marked as not working yet (work in progress).

## [0.4.2] - 2026-10-27

### Added
- `install.sh`: one installer for all distributions. On Arch and derivatives it builds and
  installs the pacman package; on Debian/Ubuntu, Fedora and openSUSE it installs the
  dependencies and RazerTux into `/opt/razertux` (untested, work in progress).
  `./install.sh --uninstall` removes RazerTux again and offers to restore the mouse first.
- README: step-by-step install, update and uninstall guide for each distribution.

### Changed
- README: notes that RazerTux is a work in progress and was vibe coded with an AI assistant.

### Removed
- README: upgrade notes for old versions.

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
