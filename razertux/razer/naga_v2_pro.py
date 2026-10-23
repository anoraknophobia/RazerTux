"""Razer Naga V2 Pro: read commands, write commands and device mode.

Byte layouts follow OpenRazer (driver/razerchromacommon.c, razermouse_driver.c)
for USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRED/WIRELESS: transaction id 0x1F everywhere.
"""
from ..config.model import LightingZone
from . import commands as C
from .report import build_report
from .transport import RazerError, Transport

VARSTORE = 0x01
NOSTORE = 0x00
TXN = 0x1F

# LED ids (OpenRazer razercommon.h: SCROLL_WHEEL_LED, LOGO_LED, BACKLIGHT_LED)
SCROLL = 0x01
LOGO = 0x04
THUMB = 0x05
ZONE_LED = {"logo": LOGO, "scroll": SCROLL, "thumb": THUMB}

# supported poll rates (razer_chroma_misc_set_polling_rate, standard variant)
_POLL_BYTE = {1000: 0x01, 500: 0x02, 125: 0x08}

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


def _check_dpi(value: int) -> None:
    if not isinstance(value, int) or not 100 <= value <= 30000:
        raise ValueError(f"DPI {value!r} ausserhalb 100-30000")


class NagaV2Pro:
    def __init__(self, transport: Transport):
        self._t = transport

    def _get(self, cmd_class: int, cmd_id: int, args: bytes, size: int) -> bytes:
        report = build_report(cmd_class, cmd_id, args, size=size, txn=TXN)
        return self._t.query(report).args

    def _run(self, built: tuple[int, int, bytes, int]) -> bytes:
        """Send a commands.py builder result and return the response args."""
        return self._get(*built)

    def _read(self, name: str, *builder_args):
        """Send builder ``name`` and run its PARSER on the response."""
        return C.PARSERS[name](self._run(C.BUILDERS[name](*builder_args)))

    def device_mode(self) -> int:
        """0x00 normal, 0x03 driver mode (top buttons as vendor reports)."""
        return self._read("razer_chroma_standard_get_device_mode")[0]

    def set_device_mode(self, mode: int) -> None:
        self._run(C.razer_chroma_standard_set_device_mode(mode))

    def firmware(self) -> str:
        return self._read("razer_chroma_standard_get_firmware_version")

    def serial(self) -> str:
        return self._read("razer_chroma_standard_get_serial")

    def dpi(self) -> tuple[int, int]:
        return self._read("razer_chroma_misc_get_dpi_xy", NOSTORE)

    def dpi_stages(self) -> tuple[int, list[tuple[int, int]]]:
        """Onboard stage table (class 0x04 id 0x83, the one the back top button
        cycles; 0x86 from OpenRazer only shows one stage on this firmware).
        Entries carry 1-based ids; returns (1-based position of the active id, stages).
        An active id missing from the table is reported as position 1."""
        a = self._get(0x04, 0x83, bytes([VARSTORE]), 0x26)
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

    def poll_rate(self) -> int:
        a = self._run(C.razer_chroma_misc_get_polling_rate())
        hz = C.PARSERS["razer_chroma_misc_get_polling_rate"](a)
        if not hz:
            raise ValueError(f"unknown poll rate byte {a[0]:#04x}")
        return hz

    def brightness(self, led: int) -> int:
        return self._read("razer_chroma_extended_matrix_get_brightness", VARSTORE, led)  # echo: varstore, led, level

    def battery(self) -> int:
        return round(self._read("razer_chroma_misc_get_battery_level") * 100 / 255)

    def charging(self) -> bool:
        return self._read("razer_chroma_misc_get_charging_status") != 0

    def idle_time(self) -> int:
        return self._read("razer_chroma_misc_get_idle_time")

    def low_battery_threshold(self) -> int:
        return round(self._read("razer_chroma_misc_get_low_battery_threshold") * 100 / 255)

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
        self._get(cls, cid, bytes([store]) + args[1:], size)

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
        self._get(0x04, 0x06, bytes(args), 0x26)

    def repair_active_stage(self) -> tuple[int, int] | None:
        """Onboard stage table (class 0x04 id 0x83 VARSTORE, the one the back top
        button cycles; entries carry 1-based stage ids). If the active id is not in
        the table, make the first stage active again (VARSTORE, entries unchanged).
        Seen on hardware: active 0x0b with 5 stages, the mouse then ran at 52582 DPI.
        Returns the active stage's (x, y), or None if the table is unusable."""
        a = self._get(0x04, 0x83, bytes([VARSTORE]), 0x26)
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
        self._get(0x04, 0x06, bytes([VARSTORE, first, count]) + b"".join(entries), 0x26)
        return dpis[first]

    def set_poll_rate(self, hz: int) -> None:
        """razer_chroma_misc_set_polling_rate: class 0x00 id 0x05 size 1 (no storage arg)."""
        if hz not in _POLL_BYTE:
            raise ValueError(f"Abfragerate {hz!r} nicht in {sorted(_POLL_BYTE)}")
        self._run(C.razer_chroma_misc_set_polling_rate(hz))

    def set_brightness(self, led: int, value: int, *, store: int = NOSTORE) -> None:
        """razer_chroma_extended_matrix_brightness: class 0x0F id 0x04 size 3."""
        if not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError(f"Helligkeit {value!r} ausserhalb 0-255")
        self._run(C.razer_chroma_extended_matrix_brightness(store, led, value))

    def set_effect(self, led: int, zone: LightingZone, *, store: int = NOSTORE) -> None:
        """razer_chroma_extended_matrix_effect_*: class 0x0F id 0x02 (OpenRazer's per-LED
        path for this model). Wave and spectrum take no colour."""
        eff, rgb = zone.effect, tuple(zone.color)
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

    def set_idle_time(self, seconds: int) -> None:
        """razer_chroma_misc_set_idle_time: class 0x07 id 0x03 size 2 (no storage arg)."""
        if not isinstance(seconds, int) or not 60 <= seconds <= 900:
            raise ValueError(f"Leerlaufzeit {seconds!r} ausserhalb 60-900 s")
        self._run(C.razer_chroma_misc_set_idle_time(seconds))

    def set_low_battery_threshold(self, percent: int) -> None:
        """razer_chroma_misc_set_low_battery_threshold: class 0x07 id 0x01 size 1
        (no storage arg). Raw 0x0C=5%, 0x26=15%, 0x3F=25%, i.e. raw = percent*255//100."""
        if not isinstance(percent, int) or not 5 <= percent <= 25:
            raise ValueError(f"Akku-Schwelle {percent!r} ausserhalb 5-25 %")
        self._run(C.razer_chroma_misc_set_low_battery_threshold(percent * 255 // 100))

    def snapshot(self) -> dict:
        """All readable state; each field independently None on any error."""

        def safe(fn, *a):
            try:
                return fn(*a)
            except (RazerError, OSError, ValueError, IndexError):
                return None

        def stages():
            active, lst = self.dpi_stages()
            return {"active": active, "stages": [list(s) for s in lst]}

        dpi = safe(self.dpi)
        return {
            "firmware": safe(self.firmware),
            "serial": safe(self.serial),
            "dpi": list(dpi) if dpi else None,
            "dpi_stages": safe(stages),
            "poll_rate": safe(self.poll_rate),
            "brightness": {n: safe(self.brightness, led) for n, led in ZONE_LED.items()},
            "battery": safe(self.battery),
            "charging": safe(self.charging),
            "idle_time": safe(self.idle_time),
            "low_battery_threshold": safe(self.low_battery_threshold),
        }
