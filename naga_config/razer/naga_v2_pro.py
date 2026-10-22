"""Razer Naga V2 Pro: read commands, write commands and device mode.

Byte layouts follow OpenRazer (driver/razerchromacommon.c, razermouse_driver.c)
for USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRED/WIRELESS: transaction id 0x1F everywhere.
"""
from ..config.model import LightingZone
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

# poll rate byte -> Hz (razer_attr_read_poll_rate, standard variant)
_POLL_HZ = {0x01: 1000, 0x02: 500, 0x08: 125}


# poll rate Hz -> byte (razer_chroma_misc_set_polling_rate)
_POLL_BYTE = {hz: raw for raw, hz in _POLL_HZ.items()}

# Defaults OpenRazer does not expose per call (documented, see docs/hardware-notes.md)
WAVE_DIRECTION = 1  # 0x01 = right/left per firmware; OpenRazer accepts 0x00..0x02
REACTIVE_SPEED = 2  # OpenRazer: 0x01 (fast) .. 0x04 (slow)
_WAVE_SPEED = 0x28  # fixed in razer_chroma_extended_matrix_effect_wave

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

    def device_mode(self) -> int:
        """0x00 normal, 0x03 driver mode (top buttons as vendor reports)."""
        return self._get(0x00, 0x84, b"", 0x02)[0]

    def set_device_mode(self, mode: int) -> None:
        self._get(0x00, 0x04, bytes([mode, 0x00]), 0x02)

    def firmware(self) -> str:
        a = self._get(0x00, 0x81, b"", 0x02)
        return f"v{a[0]}.{a[1]}"

    def serial(self) -> str:
        a = self._get(0x00, 0x82, b"", 0x16)
        return a[:22].split(b"\x00")[0].decode("ascii", "replace")

    def dpi(self) -> tuple[int, int]:
        a = self._get(0x04, 0x85, bytes([NOSTORE]), 0x07)
        return (a[1] << 8) | a[2], (a[3] << 8) | a[4]

    def dpi_stages(self) -> tuple[int, list[tuple[int, int]]]:
        a = self._get(0x04, 0x86, bytes([VARSTORE]), 0x26)
        active, count = a[1], a[2]
        stages = []
        for i in range(count):
            o = 4 + 7 * i
            if o + 4 > len(a):
                break
            stages.append(((a[o] << 8) | a[o + 1], (a[o + 2] << 8) | a[o + 3]))
        return active, stages

    def poll_rate(self) -> int:
        raw = self._get(0x00, 0x85, b"", 0x01)[0]
        if raw not in _POLL_HZ:
            raise ValueError(f"unknown poll rate byte {raw:#04x}")
        return _POLL_HZ[raw]

    def brightness(self, led: int) -> int:
        return self._get(0x0F, 0x84, bytes([VARSTORE, led]), 0x03)[2]  # echo: varstore, led, level

    def battery(self) -> int:
        raw = self._get(0x07, 0x80, b"", 0x02)[1]
        return round(raw * 100 / 255)

    def charging(self) -> bool:
        return self._get(0x07, 0x84, b"", 0x02)[1] != 0

    def idle_time(self) -> int:
        a = self._get(0x07, 0x83, b"", 0x02)
        return (a[0] << 8) | a[1]

    def low_battery_threshold(self) -> int:
        raw = self._get(0x07, 0x81, b"", 0x01)[0]
        return round(raw * 100 / 255)

    def scroll_profile(self, *, allow_unverified: bool = False) -> int | None:
        """Read-only; not in OpenRazer. None if the firmware rejects it.

        Disabled (R16): this unverified 0x02/0x98 get is suspected of changing the
        wheel haptics on the real mouse. Needs allow_unverified=True until Task 11."""
        if not allow_unverified:
            raise RuntimeError("deaktiviert bis Task 11 (R16)")
        try:
            return self._get(0x02, 0x98, bytes([VARSTORE]), 0x02)[1]
        except RazerError:
            return None

    # -- write commands ----------------------------------------------------
    # Every command with a storage argument takes store=NOSTORE by default (RAM
    # only); restore_backup passes VARSTORE to write back what was stored.

    def set_dpi(self, x: int, y: int, *, store: int = NOSTORE) -> None:
        """razer_chroma_misc_set_dpi_xy: class 0x04 id 0x05 size 7."""
        _check_dpi(x)
        _check_dpi(y)
        self._get(0x04, 0x05, bytes([store, x >> 8, x & 0xFF, y >> 8, y & 0xFF, 0, 0]), 0x07)

    def set_dpi_stages(self, active: int, stages: list[tuple[int, int]], *,
                       store: int = NOSTORE) -> None:
        """razer_chroma_misc_set_dpi_stages: class 0x04 id 0x06 size 0x26.
        active is 1-based; 1-5 stages, 100-30000 in steps of 50."""
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
            args += bytes([i, x >> 8, x & 0xFF, y >> 8, y & 0xFF, 0, 0])
        self._get(0x04, 0x06, bytes(args), 0x26)

    def set_poll_rate(self, hz: int) -> None:
        """razer_chroma_misc_set_polling_rate: class 0x00 id 0x05 size 1 (no storage arg)."""
        if hz not in _POLL_BYTE:
            raise ValueError(f"Abfragerate {hz!r} nicht in {sorted(_POLL_BYTE)}")
        self._get(0x00, 0x05, bytes([_POLL_BYTE[hz]]), 0x01)

    def set_brightness(self, led: int, value: int, *, store: int = NOSTORE) -> None:
        """razer_chroma_extended_matrix_brightness: class 0x0F id 0x04 size 3."""
        if not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError(f"Helligkeit {value!r} ausserhalb 0-255")
        self._get(0x0F, 0x04, bytes([store, led, value]), 0x03)

    def set_effect(self, led: int, zone: LightingZone, *, store: int = NOSTORE) -> None:
        """razer_chroma_extended_matrix_effect_*: class 0x0F id 0x02 (OpenRazer's per-LED
        path for this model). Wave and spectrum take no colour."""
        eff, (r, g, b) = zone.effect, zone.color
        head = bytes([store, led])
        if eff == "off":
            args = head + bytes([0x00, 0, 0, 0])
        elif eff == "static":
            args = head + bytes([0x01, 0, 0, 0x01, r, g, b])
        elif eff == "breath":
            args = head + bytes([0x02, 0x01, 0x00, 0x01, r, g, b])
        elif eff == "spectrum":
            args = head + bytes([0x03, 0, 0, 0])
        elif eff == "wave":
            args = head + bytes([0x04, WAVE_DIRECTION, _WAVE_SPEED, 0])
        elif eff == "reactive":
            args = head + bytes([0x05, 0, REACTIVE_SPEED, 0x01, r, g, b])
        else:
            raise ValueError(f"unbekannter Effekt {eff!r}")
        self._get(0x0F, 0x02, args, len(args))

    def set_idle_time(self, seconds: int) -> None:
        """razer_chroma_misc_set_idle_time: class 0x07 id 0x03 size 2 (no storage arg)."""
        if not isinstance(seconds, int) or not 60 <= seconds <= 900:
            raise ValueError(f"Leerlaufzeit {seconds!r} ausserhalb 60-900 s")
        self._get(0x07, 0x03, bytes([seconds >> 8, seconds & 0xFF]), 0x02)

    def set_low_battery_threshold(self, percent: int) -> None:
        """razer_chroma_misc_set_low_battery_threshold: class 0x07 id 0x01 size 1
        (no storage arg). Raw 0x0C=5%, 0x26=15%, 0x3F=25%, i.e. raw = percent*255//100."""
        if not isinstance(percent, int) or not 5 <= percent <= 25:
            raise ValueError(f"Akku-Schwelle {percent!r} ausserhalb 5-25 %")
        self._get(0x07, 0x01, bytes([percent * 255 // 100]), 0x01)

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
            "scroll_profile": None,  # R16: never queried until Task 11 research
        }
