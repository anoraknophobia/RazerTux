"""Razer Naga V2 Pro: read-only commands.

Byte layouts follow OpenRazer (driver/razerchromacommon.c, razermouse_driver.c)
for USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRED/WIRELESS: transaction id 0x1F everywhere.
"""
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


class NagaV2Pro:
    def __init__(self, transport: Transport):
        self._t = transport

    def _get(self, cmd_class: int, cmd_id: int, args: bytes, size: int) -> bytes:
        report = build_report(cmd_class, cmd_id, args, size=size, txn=TXN)
        return self._t.query(report).args

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

    def scroll_profile(self) -> int | None:
        """Read-only; not in OpenRazer. None if the firmware rejects it."""
        try:
            return self._get(0x02, 0x98, bytes([VARSTORE]), 0x02)[1]
        except RazerError:
            return None

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
            "scroll_profile": safe(self.scroll_profile),
        }
