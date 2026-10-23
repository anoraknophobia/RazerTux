"""Razer Naga V2 Pro: read commands, write commands and device mode.

Byte layouts follow OpenRazer (driver/razerchromacommon.c, razermouse_driver.c)
for USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRED/WIRELESS: transaction id 0x1F everywhere.
The generic parts (firmware, serial, dpi, battery, idle time, ...) come from
RazerMouse and the device database; this class keeps what was verified on the
hardware: onboard DPI stages via 0x04/0x83 with 1-based ids, repair_active_stage,
device mode, the NOSTORE storage byte for set_dpi and per-LED lighting (0x0F/0x02).
"""
from ..config.model import LightingZone
from ..devices.db import DeviceInfo, load_devices
from . import commands as C
from .mouse import RazerMouse
from .report import build_report
from .transport import Transport

VARSTORE = 0x01
NOSTORE = 0x00
TXN = 0x1F

# LED ids (OpenRazer razercommon.h: SCROLL_WHEEL_LED, LOGO_LED, BACKLIGHT_LED)
SCROLL = 0x01
LOGO = 0x04
THUMB = 0x05
ZONE_LED = {"logo": LOGO, "scroll": SCROLL, "thumb": THUMB}

# Defaults OpenRazer does not expose per call (documented, see docs/hardware-notes.md)
WAVE_DIRECTION = 1  # 0x01 = right/left per firmware; OpenRazer accepts 0x00..0x02
REACTIVE_SPEED = 2  # OpenRazer: 0x01 (fast) .. 0x04 (slow)

# Set commands whose effect survives a replug: either they have NO varstore/storage
# argument in their layout, or the firmware ignores NOSTORE (set_dpi, see
# docs/hardware-notes.md). The daemon writes the backup's values back on a clean
# shutdown (R17); restore_backup() writes them back on request.
PERSISTENT_COMMANDS = {
    "set_dpi": True,                   # NOSTORE not honoured on hardware (Task 7, R17)
    "set_dpi_stages": True,            # untested, treated like set_dpi (R17)
    "set_poll_rate": True,             # class 0x00 id 0x05, 1 arg (rate byte)
    "set_idle_time": True,             # class 0x07 id 0x03, 2 args (seconds BE16)
    "set_low_battery_threshold": True,  # class 0x07 id 0x01, 1 arg (raw 0x0C..0x3F)
}

_EFFECTS = ("off", "static", "breath", "spectrum", "wave", "reactive")


def _check_dpi(value: int) -> None:
    if not isinstance(value, int) or not 100 <= value <= 30000:
        raise ValueError(f"DPI {value!r} ausserhalb 100-30000")


def _led(zone: str | int) -> int:
    """Zone name ("logo", "scroll", "thumb") or raw LED id."""
    return ZONE_LED[zone] if isinstance(zone, str) else zone


class NagaV2Pro(RazerMouse):
    PERSISTENT_COMMANDS = PERSISTENT_COMMANDS

    def __init__(self, transport: Transport, info: DeviceInfo | None = None):
        """``NagaV2Pro(transport)`` uses the database entry ``naga_v2_pro``;
        ``make_mouse`` passes the (flattened) info explicitly."""
        super().__init__(info if info is not None else load_devices()["naga_v2_pro"], transport)

    def _raw(self, cmd_class: int, cmd_id: int, args: bytes, size: int) -> bytes:
        report = build_report(cmd_class, cmd_id, args, size=size, txn=TXN)
        return self._t.query(report).args

    def _run(self, built: tuple[int, int, bytes, int]) -> bytes:
        """Send a commands.py builder result and return the response args."""
        return self._raw(*built)

    def _read(self, name: str, *builder_args):
        """Send builder ``name`` and run its PARSER on the response."""
        return C.PARSERS[name](self._run(C.BUILDERS[name](*builder_args)))

    # -- capabilities: lighting zones are this model's own (logo, scroll, thumb) --

    def supports(self, feature: str) -> bool:
        parts = feature.split(":")
        if parts[0] in ("brightness", "set_brightness") and len(parts) == 2:
            return parts[1] in ZONE_LED
        if parts[0] == "set_effect" and len(parts) == 3:
            return parts[1] in ZONE_LED and parts[2] in _EFFECTS
        if feature in ("device_mode", "set_device_mode"):
            return True
        return super().supports(feature)

    def brightness_zones(self) -> list[str]:
        return list(ZONE_LED)

    def device_mode(self) -> int:
        """0x00 normal, 0x03 driver mode (top buttons as vendor reports)."""
        return self._read("razer_chroma_standard_get_device_mode")[0]

    def set_device_mode(self, mode: int) -> None:
        self._run(C.razer_chroma_standard_set_device_mode(mode))

    def dpi_stages(self) -> tuple[int, list[tuple[int, int]]]:
        """Onboard stage table (class 0x04 id 0x83, the one the back top button
        cycles; 0x86 from OpenRazer only shows one stage on this firmware).
        Entries carry 1-based ids; returns (1-based position of the active id, stages).
        An active id missing from the table is reported as position 1."""
        a = self._raw(0x04, 0x83, bytes([VARSTORE]), 0x26)
        active_id, count = a[1], a[2]
        ids, stages = [], []
        for i in range(count):
            o = 3 + 7 * i
            if o + 5 > len(a):
                break
            ids.append(a[o])
            stages.append(((a[o + 1] << 8) | a[o + 2], (a[o + 3] << 8) | a[o + 4]))
        active = ids.index(active_id) + 1 if active_id in ids else 1
        return active, stages

    def brightness(self, zone: str | int) -> int:
        """zone: "logo"/"scroll"/"thumb" or an LED id (LOGO, SCROLL, THUMB)."""
        return self._read("razer_chroma_extended_matrix_get_brightness", VARSTORE, _led(zone))

    # -- write commands ----------------------------------------------------
    # Every command with a storage argument takes store=NOSTORE by default (RAM
    # only); restore_backup passes VARSTORE to write back what was stored.

    def set_dpi(self, x: int, y: int, *, store: int = NOSTORE) -> None:
        """razer_chroma_misc_set_dpi_xy: class 0x04 id 0x05 size 7."""
        _check_dpi(x)
        _check_dpi(y)
        cls, cid, args, size = C.razer_chroma_misc_set_dpi_xy(store, x, y)
        # OpenRazer's builder always writes VARSTORE; this model's callers choose the
        # storage byte (NOSTORE by default), so only that first byte is replaced.
        self._raw(cls, cid, bytes([store]) + args[1:], size)

    def set_dpi_stages(self, active: int, stages: list[tuple[int, int]], *,
                       store: int = NOSTORE) -> None:
        """razer_chroma_misc_set_dpi_stages: class 0x04 id 0x06 size 0x26.
        active and stage ids are 1-based (as Synapse writes them; 0-based ids made the
        back top button land on an invalid stage); 1-5 stages, 100-30000 in steps of 50."""
        if not 1 <= len(stages) <= 5:
            raise ValueError(f"1-5 DPI-Stufen erlaubt, nicht {len(stages)}")
        if not 1 <= active <= len(stages):
            raise ValueError(f"aktive Stufe {active} ausserhalb 1-{len(stages)}")
        for x, y in stages:
            for v in (x, y):
                _check_dpi(v)
                if v % 50:
                    raise ValueError(f"DPI {v} ist kein Vielfaches von 50")
        args = bytearray([store, active, len(stages)])
        for i, (x, y) in enumerate(stages):
            args += bytes([i + 1, x >> 8, x & 0xFF, y >> 8, y & 0xFF, 0, 0])  # 1-based ids
        self._raw(0x04, 0x06, bytes(args), 0x26)

    def repair_active_stage(self) -> tuple[int, int] | None:
        """Onboard stage table (class 0x04 id 0x83 VARSTORE, the one the back top
        button cycles; entries carry 1-based stage ids). If the active id is not in
        the table, make the first stage active again (VARSTORE, entries unchanged).
        Seen on hardware: active 0x0b with 5 stages, the mouse then ran at 52582 DPI.
        Returns the active stage's (x, y), or None if the table is unusable."""
        a = self._raw(0x04, 0x83, bytes([VARSTORE]), 0x26)
        if len(a) < 3:
            return None
        active, count = a[1], a[2]
        if not 1 <= count <= 5 or len(a) < 3 + 7 * count:
            return None
        entries = [a[3 + 7 * i: 10 + 7 * i] for i in range(count)]
        dpis = {e[0]: ((e[1] << 8) | e[2], (e[3] << 8) | e[4]) for e in entries}
        if not all(100 <= v <= 30000 for xy in dpis.values() for v in xy):
            return None
        if active in dpis:
            return dpis[active]
        first = entries[0][0]
        self._raw(0x04, 0x06, bytes([VARSTORE, first, count]) + b"".join(entries), 0x26)
        return dpis[first]

    def set_brightness(self, zone: str | int, value: int, *, store: int = NOSTORE) -> None:
        """razer_chroma_extended_matrix_brightness: class 0x0F id 0x04 size 3."""
        if not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError(f"Helligkeit {value!r} ausserhalb 0-255")
        self._run(C.razer_chroma_extended_matrix_brightness(store, _led(zone), value))

    def set_effect(self, zone: str | int, lz: LightingZone, *, store: int = NOSTORE) -> None:
        """razer_chroma_extended_matrix_effect_*: class 0x0F id 0x02 (OpenRazer's per-LED
        path for this model). Wave and spectrum take no colour."""
        led = _led(zone)
        eff, rgb = lz.effect, tuple(lz.color)
        if eff == "off":
            built = C.razer_chroma_extended_matrix_effect_none(store, led)
        elif eff == "static":
            built = C.razer_chroma_extended_matrix_effect_static(store, led, rgb)
        elif eff == "breath":
            built = C.razer_chroma_extended_matrix_effect_breathing_single(store, led, rgb)
        elif eff == "spectrum":
            built = C.razer_chroma_extended_matrix_effect_spectrum(store, led)
        elif eff == "wave":
            built = C.razer_chroma_extended_matrix_effect_wave(store, led, WAVE_DIRECTION)
        elif eff == "reactive":
            built = C.razer_chroma_extended_matrix_effect_reactive(store, led, REACTIVE_SPEED, rgb)
        else:
            raise ValueError(f"unbekannter Effekt {eff!r}")
        self._run(built)
