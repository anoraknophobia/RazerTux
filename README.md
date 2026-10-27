# RazerTux

> **Not an official Razer product; not affiliated with Razer Inc.**
> "Razer" and "Naga" are trademarks of Razer Inc.; RazerTux uses no Razer logos.
>
> Project: <https://github.com/anoraknophobia/RazerTux>

RazerTux is an unofficial Linux replacement for Razer Synapse. It
supports the Razer Naga V2 Pro and, experimentally, other Razer mice (tested on CachyOS/Arch,
KDE Plasma Wayland). It consists of a background service (`razertuxd`), a command line tool
(`razertux`) and a Qt GUI with a tray icon (`razertux-gui`). The current version is 0.3.0.
The GUI is available in English, German, Spanish and French (see "Language" below).

## Features

- **Buttons**: assignments, key combinations, macros, Hypershift, several profiles per model.
- **Free top buttons**: off = the firmware steps the scroll-wheel stage or DPI stage
  (normal mode). On = driver mode, the two top buttons can be assigned freely.
- **Learn button**: teach RazerTux the signal of a button it does not recognise (press it on the
  mouse) and assign it to a button slot. Needs the running service.
- **Performance**: DPI and DPI stages. A DPI value written permanently is restored to the
  backed-up value when the service exits cleanly.
- **Lighting**: logo and thumb pad (the scroll wheel has no LED).
- **Power/battery**: charge level, idle time, low-battery warning.
- **Scroll wheel**: shows the active scroll-wheel stage on the Naga. Changing the stage
  values or tension needs Synapse on Windows for now.
- **Multi-device**: several mice at once, each with its own per-model profiles.
- **On-screen display** when the profile or DPI stage changes (toggle in the tray menu).
- Light/dark style switch.
- UI languages: English, German, Spanish and French.

## Installation

```
cd packaging
makepkg -si
systemctl --user enable --now razertuxd.service
```

Build dependencies (`makedepends`): `python-build`, `python-installer`, `python-wheel`,
`python-setuptools`.

The service starts with the graphical session (`graphical-session.target`) and is also
started on demand via D-Bus (`io.github.razertux`). The tray icon starts automatically at
login (`/etc/xdg/autostart/razertux-tray.desktop`). If you copied a udev rule to
`/etc/udev/rules.d/` by hand earlier, please remove it; the package installs
`70-razertux.rules` to `/usr/lib/udev/rules.d/`.

The package conflicts with `openrazer-daemon` and `openrazer-driver-dkms`: both access the
same device and interfere with each other.

## Upgrading

After upgrading the package, restart the service as a normal user and restart the tray
(or log out and back in):

```
systemctl --user restart razertuxd.service
```

## Getting started

1. Connect the mouse via wireless dongle or USB and start `razertux-gui` (or click the tray
   icon). On first connect the service automatically backs up the mouse's initial state to
   `~/.config/razertux/mouse-backup.json` (an existing backup is never overwritten).
2. Create a profile and set buttons, DPI and lighting. You can switch profiles in the tray.

Command line: `razertux status` shows the mouse state and `razertux restore` restores the
backed-up state. `razertux backup` creates the backup by hand (optional, only needed without
a running service; it does not overwrite anything either). All three accept `--model` to
pick a model when several mice are connected.

## Help add your device

Is your Razer device missing, or are some of its buttons not recognized? The built-in
recorder collects a small, anonymous device report that you can send to the project.

1. Open Help, then "Record device…", or run `razertux-record`.
2. Choose your device.
3. Entering your password is optional; it only unlocks extra read-only information.
4. Press the buttons of the device, one after another.
5. Optionally run the light test.
6. Review the report and submit it on GitHub (you need a GitHub account).

Privacy promise: the report contains no serial numbers, no user or host names and no
paths. Only the distinct button signals are recorded, never typed text. Nothing is
persisted on the device, and the light test uses NOSTORE only (it is not saved to the
device and disappears on its own).

### For maintainers

`tools/import_device_report.py` reads a submitted device report and turns it into entries
for the device database.

## Language

*View → Language* (also in the tray menu): system language, English, German, Spanish,
French. "System language" uses the language of the system and falls back to English if it
is not supported. The GUI switches to a new language after a restart ("Restart now" restarts
it immediately); the service switches immediately (battery warning, configuration errors).
The setting is stored as `"language"` in `~/.config/razertux/settings.json`.

English is the source language. Translations are JSON files in `razertux/i18n/<lang>.json`
(`{"context": {"English text": "translation"}}`); `tests/test_i18n.py` checks that every
text is translated and the placeholders match.

## Restoring the original state

```
razertux restore
```

(or "Restore original state" in the tray) writes the backed-up values back: DPI, DPI stages,
polling rate, brightness and the power settings (idle time, low-battery threshold). Effects
and colors are not included; the mouse resets them itself when unplugged and plugged in
again. Use `razertux status` to compare the result against the backup. The backup is stored
in `~/.config/razertux/mouse-backup.json`.

## Uninstalling

```
razertux restore
systemctl --user disable --now razertuxd.service
sudo pacman -Rns razertux
rm -r ~/.config/razertux
```

Run `restore` before uninstalling, while the service is still there. Removing
`~/.config/razertux` also deletes the backup and the profiles.

## Supported mice

- **Razer Naga V2 Pro**: verified (wireless dongle and USB), full feature set.
- **All other Razer mice from the OpenRazer database** (88 models / 117 USB product IDs, see
  `razertux/devices/openrazer_mice.json`): **experimental**. RazerTux first only reads (read
  probe) and backs up the device state before anything is written. On these mice, DPI,
  polling rate and similar settings are available depending on the model; these mice are
  untested, so there is no guarantee.
- Reporting problems: choose **"Copy diagnostics"** in the GUI and paste the text into a
  GitHub issue (<https://github.com/anoraknophobia/RazerTux/issues>).
- The database comes from OpenRazer (GPL-2.0), see `OPENRAZER-NOTICE` (installed to
  `/usr/share/licenses/razertux/`). The udev rule `70-razertux.rules` is generated from it
  with `python tools/import_openrazer.py --udev`.

## Laser graphics

The mouse views with button labels ("laser" lines) are original drawings (SVG templates in
the package). No Razer images or logos are used.

## On-screen display (OSD)

When the profile or the DPI stage changes, RazerTux briefly shows a confirmation on screen.
The display can be switched on and off in the tray menu.

## Known limits

- Bluetooth: normal input only, no configuration. Use the wireless dongle or USB.
- Scroll-wheel stage values (haptics) cannot be changed, only the active stage is shown.
- `kill -9` on the service skips the cleanup. systemd restarts the service; this only
  affects "Free top buttons" (driver mode): until the restart the top buttons do not react,
  afterwards the service sets the mode to match the profile again. Without a running
  service, unplugging and replugging the mouse helps.
- `dbus-next` is deprecated as of Python 3.15; a replacement will be needed then.
- Running an OpenRazer service in parallel is not supported (package conflict).
- More hardware details: `docs/hardware-notes.md`.

## Credits

Much of the knowledge about the HID protocol of Razer mice, and the device data, comes from
[OpenRazer](https://github.com/openrazer/openrazer) (GPL-2.0). Many thanks to the OpenRazer
project. The app icon (penguin) is an original drawing.

## License

This program is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation; either version 2 of the License, or (at your option) any later
version (GPL-2.0-or-later). See `LICENSE` for the GPL-2.0 text.
