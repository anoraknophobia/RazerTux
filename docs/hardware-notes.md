# Hardware notes

## NOSTORE check of the write commands (Task 7)

All set commands with a storage flag send `NOSTORE` (0x00) at arg index 0; only
`restore_backup` uses `VARSTORE` (0x01). Commands without a storage flag are listed in
`PERSISTENT_COMMANDS` (`razertux/razer/naga_v2_pro.py`).

Procedure per command: set a value, stop the service, unplug and replug the mouse, compare
the value via `razertux status` or by visual inspection. Record the result here.

| Command | NOSTORE honored? | Notes |
|---|---|---|
| set_dpi (0x04/0x05) | **no** | 2026-10-22: set 3000 with NOSTORE -> still 3000 after unplugging. DPI takes effect permanently; the service must write the backed-up DPI back on exit |
| set_dpi_stages (0x04/0x06) | unchecked | treat as permanent like set_dpi, to be safe |
| set_effect off/static/breath/spectrum/wave/reactive (0x0F/0x02), logo | yes (static checked) | static red visible without the mode switcher; original restored after unplugging. Other effects unchecked |
| set_effect, thumb | yes (static checked) | static blue visible, original after unplugging |
| set_brightness (0x0F/0x04), logo | yes | 255 visible, stored value stayed 191 |
| set_brightness, thumb | yes | same as logo |
| set_poll_rate (0x00/0x05) | n/a (no flag) | persistent, reset via `restore` |
| set_idle_time (0x07/0x03) | n/a (no flag) | persistent |
| set_low_battery_threshold (0x07/0x01) | n/a (no flag) | persistent |
| restore (CLI/D-Bus, VARSTORE) | ok (CLI, hidraw path) | status afterwards = backup (dpi 1800, stage 2500, 500 Hz, 191/191, 300 s, 5 %) |

## Open assumptions

- Static: for the device-wide static effect (all LEDs), OpenRazer first sends a
  "mode switcher" (0x0F/0x02, args `00 00 08 00 00 00`) and then `0x0F/0x03` (Trinity).
  The per-LED path (`razer_attr_write_matrix_effect_static_common`) only uses `0x0F/0x02`
  with an LED ID. We send the per-LED path without the preliminary command. If static has no
  effect: try adding the preliminary command.
- Wave direction 1 and reactive speed 2 are default values (`WAVE_DIRECTION`,
  `REACTIVE_SPEED`); OpenRazer allows wave 0..2, reactive 1..4.
- Battery threshold: raw value = percent*255//100 (5 % = 0x0C, 15 % = 0x26, 25 % = 0x3F).
- When setting DPI, OpenRazer ignores its storage argument and always sends VARSTORE;
  we send NOSTORE (layout otherwise identical). If the mouse rejects NOSTORE for 0x04/0x05,
  it is recorded here.

## Unchecked (0.3.0)

- Plates 6/2 of the Naga graphic (6-button and 2-button side plate) are an assumption: the
  layout was derived from the 12-button plate, not checked against the hardware.
- The generic driver (database from OpenRazer) has only been checked against the Naga V2 Pro;
  on other hardware it is untested.

## Scroll-wheel haptics (incident 2026-10-22)

- In driver mode (0x03) the wheel feels different; the mouse apparently expects the host to
  set the haptics.
- After testing, the haptics were also changed in normal mode and after unplugging (before:
  very fine and crisp; now: weaker, coarser). Suspicion: the unconfirmed command 0x02/0x98
  (from OpenRazer PR #2741) writes instead of reading. Blocked since then (R16) until
  Task 11 - cleared up, see "Scroll-wheel stages" below.

## Scroll-wheel stages (Task 11, determined with the user at the device)

- **Normal mode (0x00):** The **front** top button (TOP_1) makes the firmware step the
  scroll-wheel haptic stage, the **rear** one (TOP_2) steps the DPI stage. The firmware
  reports this as an input report on the if01 hidraw node (the same one the vendor reader
  reads):
  - Stage changed: `05 3a <slot> <preset>` - observed `02 81`, `03 82`, `04 84`,
    `05 85`, `06 86`. Slot 6 is "Ultra-fine" (confirmed by the user); the mapping of the
    other slots to Synapse names is unknown.
  - DPI changed: `05 02 <x hi> <x lo> <y hi> <y lo>` (e.g. `05 02 09 c4 09 c4` = 2500).
  - Side plate contact: `05 0e <plate>` - `03` with the 12-key plate attached, `00` while
    it has no contact (2026-11-04: dirty magnets made it flicker while pressing; keys held
    during `00` never got a release, so key 8 stuck system-wide). Held side buttons are
    released on `00`. Values for the 6- and 2-key plates are unknown.
  - Ignored: `05 31 ...` (periodic).
- **Driver mode (0x03):** TOP_1/TOP_2 send `04 57` / `04 52` (release `04 00`); the
  firmware then does **not** step any stages. The wheel haptics stay as they are (checked by
  the user).
- `0x02/0x98` only returns its argument (harmless, useless); `NagaV2Pro.scroll_profile()`
  and the R16 block have been removed.
- **Read/set the active slot (Synapse USB capture, 2026-10-27):** Synapse first reads
  class `0x15` id `0x80` with args `01 00` (reply `01 <slot>`), then sets class `0x15` id
  `0x00` with args `01 <slot>` (size 2). The mouse answers each with the usual `05 3a`
  notification. Slot numbers match the notifications; the preset ids per slot are
  1=`83`, 2=`81`, 3=`82`, 4=`84`, 5=`85`, 6=`86`.
- In the same capture the front top button sent `04 57` / `04 00` (driver-mode reports):
  Synapse runs the mouse in driver mode and steps the stage itself (read, then set).
  Afterwards the front button no longer stepped the stage in normal mode on Linux. Since
  0.5.0 RazerTux does the same as Synapse: driver mode whenever a profile is active, top
  buttons handled in software (defaults: front = next scroll stage, back = next DPI stage).
- **"Custom" stage (second capture, 2026-10-27, ten slider settings):** Synapse writes, all
  with size 80 and VARSTORE:
  1. `0x15/0x09`: `01 28 06 01 01 <steps> <tension> <steps> <tension>` followed by the
     constant `00 00 0a 56 32 64 5b 56 64 00 ff ff 00 00 09 56 32 64 5a 56 64 00 ff ff`.
  2. `0x15/0x03`: `01 06 01 01 00 00 00 00 <f> 00 00 00 00 <f> 00 21 00 20 00 03` with
     `f` = float32 `180 / steps`.
  3. Ten `0x15/0x04` packets `01 06 <segment> 00 <offset> 00 <count>` + `count` float32
     pairs (x, y): segment 1 (33 points), segment 2 (32 points) in chunks of 8, segment 3
     constant `(-720, 1) (0, 1) (720, 1)`.
  One fixed curve shape: x = shape_x * 360 / steps, y = shape_y * tension / 100 (all ten
  captures match to float rounding). Tension 0-100, steps 8-96. Afterwards the mouse is on
  Custom (`05 3a 06 86`). Implemented in 0.6.0 as `NagaV2Pro.set_custom_scroll`
  (experimental); the shape is in `razertux/razer/naga_scroll_curve.py`.
- Not used: `0x15/0x07` with args `01 <slot> 06 83 81 82 84 85 86` (sent to the wireless
  dongle only) and `0x15/0x0b` `01 07`.
- Synapse reference values (resistance/detents): Ultra-fine 65/96, Standard 60/24,
  Distinct 58/15, Adaptive 75/24, Smooth Scroll 0/32, Custom 50/90.

Implementation: the device mode follows the active profile (`remap_top_buttons`: on -> driver
mode, off -> normal mode; on connect, profile switch and reload), and on clean exit it is
always normal mode. In normal mode the service ignores the assignments of TOP_1/TOP_2. The
service evaluates `05 3a`/`05 02` in both modes (status `scroll_stage`, `dpi`) and remembers
the last slot in `~/.config/razertux/state.json`.

## DPI stages in the mouse (2026-10-23)

- The stage table that the rear top button steps through is read with `0x04/0x83` (VARSTORE,
  size 0x26): `[store, active stage ID, count, per stage: ID x_hi x_lo y_hi y_lo 00 00]`,
  IDs 1-based (written by Synapse). `0x04/0x06` writes the same table. `0x04/0x86`
  (OpenRazer) shows only 1 stage.
- Observed: active ID 0x0b with 5 stages -> the mouse ran at 52582x5696 DPI. Cause probably
  a read search over unknown commands of class 0x04. Since 0.2.1 the service immediately
  corrects a reported DPI outside 100-30000 (`repair_active_stage` + `set_dpi`).
- Reordered at the user's request: 800, 1600, 2500 (active), 3200, 6400.

## Scroll-wheel stages: search for a set command (2026-10-23)

- Report `05 3a <slot> <0x80|preset>`, preset IDs: Standard 1, Distinct 2, Ultra-fine 3,
  Adaptive 4, Smooth 5, Custom 6.
- `0x02/0x18` (OpenRazer PR #2741) -> status 0x05 (not supported). No read command returns
  tension/steps. Next step: USB capture under Windows.

## OpenRazer coexistence (2026-10-27, OpenRazer 3.12.4, kernel 7.3.0-cachyos, Naga V2 Pro wired)

- `openrazer-driver-dkms` builds; on Arch the group is `openrazer` (not `plugdev`), the
  daemon refuses to start until the user is in it (re-login/reboot needed).
- With `razermouse` bound to all three interfaces, RazerTux still works over hidraw:
  probe, backup, driver mode (`getDeviceMode` -> `"3:0"` afterwards), profile apply.
  120 parallel OpenRazer D-Bus reads during three RazerTux profile reloads: no errors on
  either side.
- Side buttons keep working. **Top buttons in driver mode:** the driver's `raw_event`
  rewrites the 16-byte `04 <code>` report into a keyboard report `01 00 <code> ...`
  (codes 0x20-0x23 become 0x68-0x6B; 0x52/0x57 stay). Recorded: TOP_2 press
  `01 00 52 00 ...`, release `01 00 00 ...`. `identify_vendor` reads that form back.
  Because it is a keyboard report now, the if01 evdev node also sends the keys (TOP_1:
  KEY_KPPLUS, i.e. a "+" typed on every press; TOP_2: KEY_UP; 0x68-0x6B: F13-F16). These
  are dropped (`NAGA_V2_PRO_IGNORED`).
- D-Bus objects: `/org/razer` (`razer.devices.getDevices`, `razer.daemon.version`),
  `/org/razer/device/<serial>` with interfaces `razer.device.dpi` (`getDPI ai`,
  `setDPI qq`, `getDPIStages (ya(qq))`, `setDPIStages ya(qq)`, `maxDPI i`),
  `razer.device.misc` (`getVidPid ai`, `getSerial s`, `getFirmware s`, `getPollRate i`,
  `setPollRate q`, `getDeviceMode s`, `setDeviceMode yy`, …), `razer.device.power`
  (`getBattery d`, `isCharging b`, `get/setIdleTime q`, `get/setLowBatteryThreshold y`),
  `razer.device.lighting.logo` (`setLogoNone/Static yyy/BreathSingle yyy/BreathDual/
  BreathRandom/Reactive yyyy/Spectrum/Wave i/Brightness d`, `getLogoBrightness d`,
  `getLogoEffect s`), `razer.device.lighting.chroma` (all LEDs: `setNone/Static/
  BreathSingle/Reactive/Spectrum…`, no wave) and `razer.device.lighting.brightness`
  (global `get/setBrightness d`). No scroll-wheel interface, no zone for the thumb grid.
- `getDPIStages` returns all five stages and the active id (`3 5 800 800 1600 1600 …`),
  unlike the 0x86 read noted above.

## Naga V3 Pro (2026-11-12, firmware v1.1, wired 1532:00E7)

- Not in OpenRazer (3.12.4). Same report protocol as the V2 Pro: serial, firmware, device
  mode, DPI and the onboard stage table (0x04/0x83) answer with transaction id 0x1F (0xFF
  and 0x3F answer as well). Control on interface 0; every hidraw node of the mouse answers
  feature reports.
- Interfaces: 0 mouse, 1 keyboard + vendor reports (hidraw), 2 keyboard (side keys), 3
  hidraw only (one more than the V2 Pro, nothing seen on it).
- Side keys: identical to the V2 Pro (if02, `KEY_1` .. `KEY_EQUAL`).
- **Normal mode:** the DPI buttons step the onboard stages; the firmware reports
  `05 02 <x hi> <x lo> <y hi> <y lo>` on the if01 hidraw node, as on the V2 Pro.
  Side-plate report `05 0e 03` also appears.
- **Driver mode (0x03):** if01 hidraw sends, checked one physical button at a time
  (release `04 00` for all):

  | Button | Report | ButtonId |
  |---|---|---|
  | top left, front | `04 20` | `DPI_UP` (default: DPI stage up) |
  | top left, rear | `04 21` | `DPI_DOWN` (default: DPI stage down) |
  | behind the wheel, front | `04 54` | `TOP_1` (default as V2 Pro: scroll stage cycle) |
  | behind the wheel, rear | `04 d2` | `TOP_2` (default as V2 Pro: DPI stage cycle) |
  | ring finger button, right of the right click | `04 59` | `RING` (no default) |

  The firmware then does not change the DPI.
- Onboard stage table: each 7-byte entry is `id x_hi x_lo y_hi y_lo x_hi x_lo` here (the
  last two bytes repeat x; read: `01 0190 0190 0190`, `02 0320 0320 0320` ...), so
  `set_dpi_stages` now writes x there instead of `00 00`.
- Lighting: extended matrix (0x0F) like the V2 Pro, LEDs 0x04 logo and 0x05 thumb grid,
  plus 0x01 **scroll wheel** (answers 0x0F/0x84 and lights up with 0x0F/0x02; the V2 Pro
  has no wheel LED). Since both share the `naga_v2_pro` entry, the GUI asks
  `transport.naga_v3_pro_connected()` (PID 00E7/00E8 present) to add the scroll zone and
  to pick the V3 Pro layout.
- Not checked: scroll-wheel stages/haptics, tilt wheel, battery over the dongle.
