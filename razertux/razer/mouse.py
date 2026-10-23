"""Generic Razer mouse driver: every command comes from the device database.

``RazerMouse(info, transport)`` looks each operation up in ``info.protocol``
(``devices.db.Command``: builder name(s) + transaction id) and builds the packets with
``commands.BUILDERS``. What cannot be executed exactly as OpenRazer does is
unsupported (``supports()`` is False, the method raises ``UnsupportedFeature`` before
anything is sent) – never guessed:

- a Command is executable only if every entry of ``steps`` is a known builder
  (no ``raw:*`` packet, nothing from ``commands.NOT_PORTABLE``);
- each method accepts only the builders whose argument mapping is written down here
  (taken from the razer_attr_* handlers in OpenRazer's razermouse_driver.c);
- lighting: ``steps`` is either a packet SEQUENCE or a set of ALTERNATIVES picked by
  the number of colours; ``EFFECT_PATTERNS`` spells out every supported
  steps tuple, anything else is unsupported;
- ``dpi_max == 0`` hides dpi/dpi_stages.

``info`` must be the flattened view of the connected PID (``DeviceInfo.for_pid``);
``make_mouse(info, transport, pid=...)`` flattens for you.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config.model import LightingZone
from ..devices.db import Command, DeviceInfo
from . import commands as C
from .commands import NOSTORE, VARSTORE
from .report import build_report
from .transport import RazerError, Transport

__all__ = ["EFFECT_PATTERNS", "NOSTORE", "VARSTORE", "RazerMouse", "UnsupportedFeature",
           "ZONE_LED", "make_mouse"]


class UnsupportedFeature(Exception):
    """The connected model cannot do this (or not in a way we know exactly)."""


# LED ids (OpenRazer razercommon.h) of the per-zone sysfs files (<zone>_matrix_effect_*,
# <zone>_led_brightness in razermouse_driver.c).
ZERO_LED = 0x00
SCROLL_WHEEL_LED = 0x01
LOGO_LED = 0x04
BACKLIGHT_LED = 0x05
RIGHT_SIDE_LED = 0x10
LEFT_SIDE_LED = 0x11
ZONE_LED = {"scroll": SCROLL_WHEEL_LED, "logo": LOGO_LED, "backlight": BACKLIGHT_LED,
            "left": LEFT_SIDE_LED, "right": RIGHT_SIDE_LED}

# Zone "matrix" (sysfs matrix_effect_* / matrix_brightness without prefix): the handlers
# hard-code the LED per builder family (ZERO_LED for extended matrix, BACKLIGHT_LED for
# mouse extended matrix and standard led brightness); other builders take no LED.
_MATRIX_LED = (
    ("razer_chroma_mouse_extended_matrix_", BACKLIGHT_LED),
    ("razer_chroma_extended_matrix_", ZERO_LED),
    ("razer_chroma_standard_set_led_brightness", BACKLIGHT_LED),
    ("razer_chroma_standard_get_led_brightness", BACKLIGHT_LED),
)

# CLASSIC_EFFECT_* (razercommon.h), used by razer_chroma_standard_set_led_effect
_CLASSIC = {"static": 0x00, "on": 0x00, "blinking": 0x01, "breath": 0x02, "spectrum": 0x04}

# Defaults for values OpenRazer takes from the sysfs write (same as the Naga V2 Pro)
WAVE_DIRECTION = 1
REACTIVE_SPEED = 2

DPI_MIN = 100
MAX_DPI_STAGES = 5  # RAZER_MOUSE_MAX_DPI_STAGES

# razer_attr_read_dpi: these models read with VARSTORE, every other one with NOSTORE
_GET_DPI_STORE = {"orochi_2013": VARSTORE, "imperator": VARSTORE}

# LightingZone.effect -> effect name in the database
_EFFECT_NAME = {"off": "none"}

FEATURES = ("firmware", "serial", "dpi", "dpi_stages", "poll_rate", "battery", "charging",
            "idle_time", "low_battery_threshold", "scroll_mode", "scroll_acceleration",
            "scroll_smart_reel")

# Builders each protocol key may use (one step). Not listed -> unsupported:
# - set_polling_rate2: the handler sends a second set_polling_rate2(.., 0x01) packet with a
#   per-model transaction id the database does not hold;
# - *_dpi_xy_byte: raw bytes, the driver does not define their DPI meaning.
_ACCEPTED: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in {
    "get_firmware": {"razer_chroma_standard_get_firmware_version"},
    "get_serial": {"razer_chroma_standard_get_serial"},
    "get_dpi": {"razer_chroma_misc_get_dpi_xy"},
    "set_dpi": {"razer_chroma_misc_set_dpi_xy"},
    "get_dpi_stages": {"razer_chroma_misc_get_dpi_stages"},
    "set_dpi_stages": {"razer_chroma_misc_set_dpi_stages"},
    "get_poll_rate": {"razer_chroma_misc_get_polling_rate", "razer_chroma_misc_get_polling_rate2"},
    "set_poll_rate": {"razer_chroma_misc_set_polling_rate"},
    "get_battery": {"razer_chroma_misc_get_battery_level"},
    "get_charging": {"razer_chroma_misc_get_charging_status"},
    "get_idle_time": {"razer_chroma_misc_get_idle_time"},
    "set_idle_time": {"razer_chroma_misc_set_idle_time"},
    "get_low_battery_threshold": {"razer_chroma_misc_get_low_battery_threshold"},
    "set_low_battery_threshold": {"razer_chroma_misc_set_low_battery_threshold"},
    "get_scroll_mode": {"razer_chroma_misc_get_scroll_mode"},
    "set_scroll_mode": {"razer_chroma_misc_set_scroll_mode"},
    "get_scroll_acceleration": {"razer_chroma_misc_get_scroll_acceleration"},
    "set_scroll_acceleration": {"razer_chroma_misc_set_scroll_acceleration"},
    "get_scroll_smart_reel": {"razer_chroma_misc_get_scroll_smart_reel"},
    "set_scroll_smart_reel": {"razer_chroma_misc_set_scroll_smart_reel"},
    "get_device_mode": {"razer_chroma_standard_get_device_mode"},
    "set_device_mode": {"razer_chroma_standard_set_device_mode"},
    "get_brightness": {"razer_chroma_extended_matrix_get_brightness",
                       "razer_chroma_standard_get_led_brightness",
                       "razer_chroma_misc_get_dock_brightness"},
    "set_brightness": {"razer_chroma_extended_matrix_brightness",
                       "razer_chroma_standard_set_led_brightness",
                       "razer_chroma_misc_set_dock_brightness"},
}.items()}

# brightness builders that address an LED (dock brightness does not)
_BRIGHTNESS_WITH_LED = {"razer_chroma_extended_matrix_get_brightness",
                        "razer_chroma_standard_get_led_brightness",
                        "razer_chroma_extended_matrix_brightness",
                        "razer_chroma_standard_set_led_brightness"}


# -- lighting pattern table ---------------------------------------------------------

@dataclass(frozen=True)
class _Fx:
    led: int | None  # None: zone has no LED id for this builder (only for LED-less patterns)
    effect: str  # database effect name: none/static/breath/spectrum/wave/reactive/on/blinking
    rgb: tuple[int, int, int]
    store: int


Built = C.Built


@dataclass(frozen=True)
class _OwnTxn:
    """A packet the OpenRazer handler sends with its own transaction id instead of the
    Command's, e.g. one whose transaction_id is set to 0xFF explicitly."""
    built: Built
    txn: int


@dataclass(frozen=True)
class _Pattern:
    needs_led: bool
    effects: frozenset[str]  # effects this pattern can express
    # packets in send order; a plain Built uses Command.txn, _OwnTxn its own
    build: Callable[[_Fx], list[Built | _OwnTxn]]


def _b(name: str) -> Callable[..., Built]:
    return C.BUILDERS[name]


def _single(name: str, needs_led: bool, effect: str, args: Callable[[_Fx], tuple]) -> tuple:
    return (name,), _Pattern(needs_led, frozenset({effect}), lambda f: [_b(name)(*args(f))])


def _family(prefix: str, *, led: bool) -> list[tuple]:
    """Single-builder effects of one matrix family; ``led``: builders take (store, led, ...)."""
    pre = (lambda f: (f.store, f.led)) if led else (lambda f: ())
    table = [
        _single(prefix + "none", led, "none", pre),
        _single(prefix + "static", led, "static", lambda f: (*pre(f), f.rgb)),
        _single(prefix + "spectrum", led, "spectrum", pre),
        _single(prefix + "reactive", led, "reactive", lambda f: (*pre(f), REACTIVE_SPEED, f.rgb)),
        _single(prefix + "wave", led, "wave", lambda f: (*pre(f), WAVE_DIRECTION)),
    ]
    # razer_attr_write_*matrix_effect_breath*: switch(count) 3 -> single, 6 -> dual,
    # default -> random. ALTERNATIVES: one colour picks *_breathing_single.
    alts = (prefix + "breathing_single", prefix + "breathing_dual", prefix + "breathing_random")
    table.append((alts, _Pattern(led, frozenset({"breath"}),
                                 lambda f: [_b(alts[0])(*pre(f), f.rgb)])))
    return [(k, p) for k, p in table if all(s in C.BUILDERS for s in k)]


_STATE, _RGB, _LEFF = ("razer_chroma_standard_set_led_state", "razer_chroma_standard_set_led_rgb",
                       "razer_chroma_standard_set_led_effect")
_STD_NONE = "razer_chroma_standard_matrix_effect_none"


def _none_branch(f: _Fx) -> list[Built | _OwnTxn]:
    # matrix_effect_none_common: `if (led_id == SCROLL_WHEEL_LED) { led_state(false);
    # txn = 0x3F } else { standard_matrix_effect_none(); txn = 0xFF }` -- ALTERNATIVES
    # chosen by the LED; the database's txn (0x3F) belongs to the led_state branch only.
    if f.led == SCROLL_WHEEL_LED:
        return [_b(_STATE)(f.store, f.led, 0)]
    return [_OwnTxn(_b(_STD_NONE)(), 0xFF)]


EFFECT_PATTERNS: dict[tuple[str, ...], _Pattern] = dict([
    *_family("razer_chroma_extended_matrix_effect_", led=True),
    *_family("razer_chroma_mouse_extended_matrix_effect_", led=True),
    *_family("razer_chroma_standard_matrix_effect_", led=False),
    *_family("razer_naga_trinity_effect_", led=False),
    # none_common / on_common: one led_state packet (off / on)
    ((_STATE,), _Pattern(True, frozenset({"none", "on"}),
                         lambda f: [_b(_STATE)(f.store, f.led, int(f.effect == "on"))])),
    # static/breath/blinking_common: SEQUENCE led_state(on), led_rgb, led_effect(CLASSIC_*)
    ((_STATE, _RGB, _LEFF), _Pattern(True, frozenset({"static", "breath", "blinking"}), lambda f: [
        _b(_STATE)(f.store, f.led, 1), _b(_RGB)(f.store, f.led, f.rgb),
        _b(_LEFF)(f.store, f.led, _CLASSIC[f.effect])])),
    # spectrum_common, breath_common (mono), on_common: SEQUENCE led_state(on), led_effect
    ((_STATE, _LEFF), _Pattern(True, frozenset({"spectrum", "breath", "on"}), lambda f: [
        _b(_STATE)(f.store, f.led, 1), _b(_LEFF)(f.store, f.led, _CLASSIC[f.effect])])),
    ((_STATE, _STD_NONE), _Pattern(True, frozenset({"none"}), _none_branch)),
])


def _executable(cmd: Command | None) -> bool:
    return cmd is not None and all(s in C.BUILDERS and s not in C.NOT_PORTABLE for s in cmd.steps)


def _check_rgb(rgb) -> tuple[int, int, int]:
    rgb = tuple(rgb)
    if len(rgb) != 3 or not all(isinstance(v, int) and 0 <= v <= 255 for v in rgb):
        raise ValueError(f"Farbe {rgb!r} ungueltig")
    return rgb  # type: ignore[return-value]


class RazerMouse:
    # Set commands whose effect survives a replug: no storage argument, or OpenRazer's
    # builder/firmware writes VARSTORE anyway (set_dpi_xy always writes VARSTORE). The
    # daemon writes the backup's values back on a clean shutdown; restore_backup() on request.
    PERSISTENT_COMMANDS: dict[str, bool] = {
        "set_dpi": True,                    # builder always VARSTORE
        "set_dpi_stages": True,             # OpenRazer only ever writes VARSTORE; treated like set_dpi
        "set_poll_rate": True,              # no storage argument
        "set_idle_time": True,              # no storage argument
        "set_low_battery_threshold": True,  # no storage argument
        "set_scroll_mode": True,            # builder always VARSTORE
        "set_scroll_acceleration": True,    # builder always VARSTORE
        "set_scroll_smart_reel": True,      # builder always VARSTORE
    }

    def __init__(self, info: DeviceInfo, transport: Transport):
        self.info = info
        self._t = transport

    # -- plumbing ----------------------------------------------------------------

    def _send(self, txn: int, built: Built) -> bytes:
        cls, cid, args, size = built
        return self._t.query(build_report(cls, cid, args, size=size, txn=txn)).args

    def _command(self, key: str, accepted_as: str | None = None) -> Command | None:
        """The executable one-step Command for ``key`` (None if unsupported)."""
        cmd = self.info.protocol.get(key)
        if not _executable(cmd) or len(cmd.steps) != 1:
            return None
        if cmd.fn not in _ACCEPTED.get(accepted_as or key, ()):
            return None
        return cmd

    def _feature_ok(self, feature: str) -> bool:
        if feature not in self.info.features:
            return False
        return not (feature in ("dpi", "dpi_stages") and self.info.dpi_max <= 0)

    def _need(self, feature: str, key: str) -> Command:
        cmd = self._command(key) if self._feature_ok(feature) else None
        if cmd is None:
            raise UnsupportedFeature(f"{self.info.model}: {key}")
        return cmd

    def _get(self, feature: str, *args) -> Any:
        cmd = self._need(feature, f"get_{feature}")
        return C.PARSERS[cmd.fn](self._send(cmd.txn, C.BUILDERS[cmd.fn](*args)))

    def _set(self, feature: str, *args) -> None:
        cmd = self._need(feature, f"set_{feature}")
        self._send(cmd.txn, C.BUILDERS[cmd.fn](*args))

    def _zone_led(self, zone: str, fn: str) -> int | None:
        if zone == "matrix":
            return next((led for pre, led in _MATRIX_LED if fn.startswith(pre)), None)
        return ZONE_LED.get(zone)

    def _brightness_cmd(self, zone: str, kind: str) -> tuple[Command, tuple] | None:
        """(command, leading builder args) for get/set brightness of ``zone``."""
        cmd = self._command(f"{kind}_brightness:{zone}", f"{kind}_brightness")
        if cmd is None:
            return None
        if cmd.fn not in _BRIGHTNESS_WITH_LED:
            return cmd, ()
        led = self._zone_led(zone, cmd.fn)
        return None if led is None else (cmd, (led,))

    def _effect_plan(self, zone: str, effect: str) -> tuple[Command, _Pattern, int | None] | None:
        if zone not in self.info.zones:
            return None
        cmd = self.info.protocol.get(f"set_effect:{zone}:{effect}")
        if not _executable(cmd):
            return None
        pattern = EFFECT_PATTERNS.get(cmd.steps)
        if pattern is None or effect not in pattern.effects:
            return None
        led = None
        if pattern.needs_led:
            led = self._zone_led(zone, cmd.steps[0])
            if led is None:
                return None
        return cmd, pattern, led

    # -- capabilities ------------------------------------------------------------

    def supports(self, feature: str) -> bool:
        """``"dpi"`` etc. (readable), ``"set_dpi"`` etc. (writable), ``"brightness:<zone>"``,
        ``"set_brightness:<zone>"``, ``"set_effect:<zone>:<effect>"`` (effect as in
        LightingZone, ``"off"`` = database ``"none"``)."""
        parts = feature.split(":")
        if parts[0] in ("brightness", "set_brightness") and len(parts) == 2:
            kind = "get" if parts[0] == "brightness" else "set"
            return self._brightness_cmd(parts[1], kind) is not None
        if parts[0] == "set_effect" and len(parts) == 3:
            return self._effect_plan(parts[1], _EFFECT_NAME.get(parts[2], parts[2])) is not None
        if len(parts) != 1:
            return False
        name = feature.removeprefix("set_")
        if name not in FEATURES:
            return False
        key = feature if feature.startswith("set_") else f"get_{feature}"
        return self._feature_ok(name) and self._command(key) is not None

    def brightness_zones(self) -> list[str]:
        """Zones whose brightness can be read, in database order."""
        return [z for z in self.info.zones if self.supports(f"brightness:{z}")]

    # -- reads -------------------------------------------------------------------

    def firmware(self) -> str:
        return self._get("firmware")

    def serial(self) -> str:
        return self._get("serial")

    def dpi(self) -> tuple[int, int]:
        return self._get("dpi", _GET_DPI_STORE.get(self.info.model, NOSTORE))

    def dpi_stages(self) -> tuple[int, list[tuple[int, int]]]:
        """(active stage as the mouse reports it – 1-based as written by set_dpi_stages,
        [(x, y), ...]) from razer_chroma_misc_get_dpi_stages(VARSTORE)."""
        active, stages = self._get("dpi_stages", VARSTORE)
        return active, stages

    def poll_rate(self) -> int:
        hz = self._get("poll_rate")
        if not hz:
            raise ValueError("unknown poll rate byte")
        return hz

    def battery(self) -> int:
        return round(self._get("battery") * 100 / 255)

    def charging(self) -> bool:
        return self._get("charging") != 0

    def idle_time(self) -> int:
        return self._get("idle_time")

    def low_battery_threshold(self) -> int:
        return round(self._get("low_battery_threshold") * 100 / 255)

    def scroll_mode(self) -> int:
        return self._get("scroll_mode")

    def scroll_acceleration(self) -> bool:
        return bool(self._get("scroll_acceleration"))

    def scroll_smart_reel(self) -> bool:
        return bool(self._get("scroll_smart_reel"))

    def brightness(self, zone: str) -> int:
        plan = self._brightness_cmd(zone, "get")
        if plan is None:
            raise UnsupportedFeature(f"{self.info.model}: get_brightness:{zone}")
        cmd, led = plan
        args = (VARSTORE, *led) if led else ()  # read handlers always pass VARSTORE
        return C.PARSERS[cmd.fn](self._send(cmd.txn, C.BUILDERS[cmd.fn](*args)))

    # -- writes ------------------------------------------------------------------

    def _check_dpi(self, value: int) -> None:
        if not isinstance(value, int) or not DPI_MIN <= value <= self.info.dpi_max:
            raise ValueError(f"DPI {value!r} outside {DPI_MIN}-{self.info.dpi_max}")

    def set_dpi(self, x: int, y: int, *, store: int = NOSTORE) -> None:
        """razer_chroma_misc_set_dpi_xy. OpenRazer's builder always writes VARSTORE, so
        ``store`` has no effect here (the command is listed in PERSISTENT_COMMANDS)."""
        cmd = self._need("dpi", "set_dpi")
        self._check_dpi(x)
        self._check_dpi(y)
        self._send(cmd.txn, C.BUILDERS[cmd.fn](store, x, y))

    def set_dpi_stages(self, active: int, stages: list[tuple[int, int]], *,
                       store: int = NOSTORE) -> None:
        """razer_chroma_misc_set_dpi_stages: active 1-based, stage ids 0-based (OpenRazer)."""
        cmd = self._need("dpi_stages", "set_dpi_stages")
        if not 1 <= len(stages) <= MAX_DPI_STAGES:
            raise ValueError(f"1-{MAX_DPI_STAGES} DPI stages allowed, not {len(stages)}")
        if not 1 <= active <= len(stages):
            raise ValueError(f"active stage {active} outside 1-{len(stages)}")
        flat: list[int] = []
        for x, y in stages:
            self._check_dpi(x)
            self._check_dpi(y)
            flat += [x, y]
        self._send(cmd.txn, C.BUILDERS[cmd.fn](store, len(stages), active, flat))

    def set_poll_rate(self, hz: int) -> None:
        cmd = self._need("poll_rate", "set_poll_rate")
        if hz not in self.info.poll_rates or hz not in C._POLL_BYTE:
            raise ValueError(f"polling rate {hz!r} not in {sorted(self.info.poll_rates)}")
        self._send(cmd.txn, C.BUILDERS[cmd.fn](hz))

    def set_idle_time(self, seconds: int) -> None:
        cmd = self._need("idle_time", "set_idle_time")
        if not isinstance(seconds, int) or not 60 <= seconds <= 900:
            raise ValueError(f"idle time {seconds!r} outside 60-900 s")
        self._send(cmd.txn, C.BUILDERS[cmd.fn](seconds))

    def set_low_battery_threshold(self, percent: int) -> None:
        """Raw 0x0C=5%, 0x26=15%, 0x3F=25%, i.e. raw = percent*255//100."""
        cmd = self._need("low_battery_threshold", "set_low_battery_threshold")
        if not isinstance(percent, int) or not 5 <= percent <= 25:
            raise ValueError(f"battery threshold {percent!r} outside 5-25 %")
        self._send(cmd.txn, C.BUILDERS[cmd.fn](percent * 255 // 100))

    def set_scroll_mode(self, mode: int) -> None:
        cmd = self._need("scroll_mode", "set_scroll_mode")
        if mode not in (0, 1):  # razer_attr_write_scroll_mode: > 1 is -EINVAL
            raise ValueError(f"scroll mode {mode!r} not 0 or 1")
        self._send(cmd.txn, C.BUILDERS[cmd.fn](mode))

    def set_scroll_acceleration(self, enabled: bool) -> None:
        self._set("scroll_acceleration", bool(enabled))

    def set_scroll_smart_reel(self, enabled: bool) -> None:
        self._set("scroll_smart_reel", bool(enabled))

    def set_brightness(self, zone: str, value: int, *, store: int = NOSTORE) -> None:
        plan = self._brightness_cmd(zone, "set")
        if plan is None:
            raise UnsupportedFeature(f"{self.info.model}: set_brightness:{zone}")
        if not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError(f"brightness {value!r} outside 0-255")
        cmd, led = plan
        args = (store, *led, value) if led else (value,)
        self._send(cmd.txn, C.BUILDERS[cmd.fn](*args))

    def set_effect(self, zone: str, lz: LightingZone, *, store: int = NOSTORE) -> None:
        effect = _EFFECT_NAME.get(lz.effect, lz.effect)
        plan = self._effect_plan(zone, effect)
        if plan is None:
            raise UnsupportedFeature(f"{self.info.model}: set_effect:{zone}:{effect}")
        cmd, pattern, led = plan
        packets = pattern.build(_Fx(led, effect, _check_rgb(lz.color), store))  # all built first
        for p in packets:
            if isinstance(p, _OwnTxn):
                self._send(p.txn, p.built)
            else:
                self._send(cmd.txn, p)

    # -- snapshot ----------------------------------------------------------------

    def snapshot(self) -> dict:
        """All readable, supported state; each field independently None on any error.
        Unsupported fields are left out."""

        def safe(fn, *a):
            try:
                return fn(*a)
            except (RazerError, OSError, ValueError, IndexError):
                return None

        def stages():
            active, lst = self.dpi_stages()
            return {"active": active, "stages": [list(s) for s in lst]}

        def dpi():
            xy = self.dpi()
            return list(xy) if xy else None

        readers: dict[str, Callable[[], Any]] = {
            "firmware": self.firmware, "serial": self.serial, "dpi": dpi,
            "dpi_stages": stages, "poll_rate": self.poll_rate,
            "brightness": lambda: {z: safe(self.brightness, z) for z in self.brightness_zones()},
            "battery": self.battery, "charging": self.charging, "idle_time": self.idle_time,
            "low_battery_threshold": self.low_battery_threshold,
            "scroll_mode": self.scroll_mode, "scroll_acceleration": self.scroll_acceleration,
            "scroll_smart_reel": self.scroll_smart_reel,
        }
        return {name: safe(fn) for name, fn in readers.items()
                if name == "brightness" or self.supports(name)}


def make_mouse(info: DeviceInfo, transport: Transport, pid: int | None = None) -> RazerMouse:
    """Driver for ``info``. Pass ``pid`` to flatten ``info`` for that PID
    (``DeviceInfo.for_pid``); otherwise ``info`` must already be the flattened view."""
    if pid is not None:
        info = info.for_pid(pid)
    if info.model == "naga_v2_pro":
        from .naga_v2_pro import NagaV2Pro
        return NagaV2Pro(transport, info)
    return RazerMouse(info, transport)
