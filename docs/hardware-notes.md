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
  - Ignored: `05 31 ...` (periodic), `05 0e ...`.
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
- Also seen in that capture, not used yet: `0x15/0x09` and `0x15/0x03` (80 bytes) when
  the Custom stage was edited, `0x15/0x04` (80 bytes, float values, per slot) and, sent to
  the wireless dongle only, `0x15/0x07` with args `01 <slot> 06 83 81 82 84 85 86`.
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
