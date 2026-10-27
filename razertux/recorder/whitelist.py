"""What the record helper may send to a device: pure get commands and NOSTORE light-test packets.

``GuardTransport`` is the last line of defence: every report is checked BEFORE it is passed on.
Allowed are only
- get commands (command id with bit 7 set) from the fixed table ``GET_IDS``, never the serial
  (class 0x00 id 0x82);
- inside ``light_mode()``: the light-test packet ids ``LIGHT_IDS`` with the storage byte
  (argument 0 for every one of them) set to NOSTORE.
Anything else raises ``BlockedCommand`` and nothing is sent. The daemon may use the same control
node at the same time: a reply whose (class, id) is not the request's is dropped (``RazerError``).

``DeviceSession`` builds the per-device read table and light plans from the device database:
- mice: ``razer.mouse.make_mouse`` read methods (serial is never read);
- other kinds: the database's get_firmware / get_battery / get_charging / get_brightness:<zone>
  Commands, only with a known get builder and fully resolved arguments;
- unknown PIDs: only firmware (0x00/0x81), tried with transaction ids 0x1F, 0x3F, 0xFF; the helper
  opens the node only when its HID descriptor declares a 90-byte feature report
  (``has_razer_feature_report``).
Light: mice via ``RazerMouse.set_effect(zone, static green / off, store=NOSTORE)``, dry-run first
and only offered when every packet of BOTH states is a NOSTORE light packet; other kinds only
``db.light_test_command`` (NOSTORE at its storage index), "off" being that command with black.
The exact packets are computed once at open time and are the only light bytes ever sent.
"""
from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from razertux.config.model import LightingZone
from razertux.devices import db
from razertux.razer import commands as C
from razertux.razer.mouse import UnsupportedFeature, make_mouse
from razertux.razer.report import STATUS_OK, RazerResponse, build_report, parse_report
from razertux.razer.transport import RazerError, Transport

NOSTORE = 0x00
SERIAL_ID = (0x00, 0x82)
UNKNOWN_FIRMWARE_TXNS = (0x1F, 0x3F, 0xFF)

# (class, id) of every get builder the read table may use (razer/commands.py, naga_v2_pro.py)
GET_IDS: frozenset[tuple[int, int]] = frozenset({
    (0x00, 0x81),  # firmware
    (0x00, 0x84),  # device mode
    (0x00, 0x85),  # polling rate
    (0x00, 0xC0),  # polling rate 2
    (0x02, 0x94), (0x02, 0x96), (0x02, 0x97),  # scroll mode / acceleration / smart reel
    (0x03, 0x83),  # standard led brightness
    (0x04, 0x81),  # dpi byte
    (0x04, 0x83),  # Naga V2 Pro onboard dpi stages
    (0x04, 0x85),  # dpi
    (0x04, 0x86),  # dpi stages
    (0x07, 0x80), (0x07, 0x81), (0x07, 0x82), (0x07, 0x83), (0x07, 0x84),  # battery .. charging
    (0x0E, 0x84),  # blade brightness
    (0x0F, 0x84),  # extended matrix brightness
})

# light-test set packets; for all of them argument 0 is the storage byte
LIGHT_IDS: frozenset[tuple[int, int]] = frozenset({
    (0x0F, 0x02),  # extended matrix effect (store, led, effect, ...)
    (0x03, 0x0D),  # mouse extended matrix effect (store, led, effect, ...)
    (0x03, 0x00),  # standard led state (store, led, state)
    (0x03, 0x01),  # standard led rgb (store, led, r, g, b)
    (0x03, 0x02),  # standard led effect (store, led, effect)
})

GREEN = (0, 255, 0)
BLACK = (0, 0, 0)
LIGHT_STATES = ("green", "off")

MOUSE_READS = ("firmware", "dpi", "dpi_stages", "poll_rate", "battery", "charging", "idle_time",
               "low_battery_threshold", "scroll_mode", "scroll_acceleration", "scroll_smart_reel",
               "device_mode")

# database protocol key -> read name, for non-mouse kinds
_OTHER_READS = {"get_firmware": "firmware", "get_battery": "battery", "get_charging": "charging"}
_OTHER_GET_FNS = frozenset({
    "razer_chroma_standard_get_firmware_version", "razer_chroma_misc_get_battery_level",
    "razer_chroma_misc_get_charging_status", "razer_chroma_extended_matrix_get_brightness",
    "razer_chroma_standard_get_led_brightness", "razer_chroma_misc_get_blade_brightness",
    "razer_chroma_misc_get_dock_brightness",
})

_READ_ERRORS = (RazerError, OSError, ValueError, IndexError, KeyError, TypeError, UnsupportedFeature)


class BlockedCommand(Exception):
    """A report outside the whitelist; it was not sent."""


def is_allowed_get(report: bytes) -> bool:
    r = parse_report(report)
    key = (r.cmd_class, r.cmd_id)
    return bool(r.cmd_id & 0x80) and key != SERIAL_ID and key in GET_IDS


def is_allowed_light(report: bytes) -> bool:
    r = parse_report(report)
    return (r.cmd_class, r.cmd_id) in LIGHT_IDS and len(r.args) > 0 and r.args[0] == NOSTORE


RAZER_REPORT_BYTES = 90


def has_razer_feature_report(descriptor: bytes) -> bool:
    """True when the HID report descriptor declares a feature report of at least 90 bytes (the
    Razer control report). Short items only; long items are skipped; a truncated descriptor ends
    the scan."""
    size = count = report_id = 0
    stack: list[tuple[int, int, int]] = []
    feature: dict[int, int] = {}  # report id -> bits
    d, i = bytes(descriptor), 0
    while i < len(d):
        prefix = d[i]
        if prefix == 0xFE:  # long item: 0xFE, data size, tag, data
            if i + 1 >= len(d):
                break
            i += 3 + d[i + 1]
            continue
        n = (0, 1, 2, 4)[prefix & 0x03]
        if i + 1 + n > len(d):
            break
        value = int.from_bytes(d[i + 1:i + 1 + n], "little")
        tag = prefix & 0xFC
        if tag == 0x74:    # Report Size (bits)
            size = value
        elif tag == 0x94:  # Report Count
            count = value
        elif tag == 0x84:  # Report ID
            report_id = value
        elif tag == 0xA4:  # Push
            stack.append((size, count, report_id))
        elif tag == 0xB4 and stack:  # Pop
            size, count, report_id = stack.pop()
        elif tag == 0xB0:  # Feature main item
            feature[report_id] = feature.get(report_id, 0) + size * count
        i += 1 + n
    return any(bits >= RAZER_REPORT_BYTES * 8 for bits in feature.values())


class GuardTransport:
    def __init__(self, inner: Transport):
        self._inner = inner
        self._light = False
        self.responses: list[bytes] = []

    @contextlib.contextmanager
    def light_mode(self):
        self._light = True
        try:
            yield
        finally:
            self._light = False

    def query(self, report: bytes) -> RazerResponse:
        report = bytes(report)
        if len(report) != 90:
            raise BlockedCommand("bad report length")
        if not (is_allowed_get(report) or (self._light and is_allowed_light(report))):
            raise BlockedCommand("command not on the whitelist")
        resp = self._inner.query(report)
        sent = parse_report(report)
        if (resp.cmd_class, resp.cmd_id) != (sent.cmd_class, sent.cmd_id):
            # the daemon shares the control node: this reply answers somebody else's command
            raise RazerError(f"reply {resp.cmd_class:#04x}/{resp.cmd_id:#04x} does not match "
                             f"the request {sent.cmd_class:#04x}/{sent.cmd_id:#04x}")
        self.responses.append(bytes(resp.args))
        return resp


class _Capture:
    """Dry-run transport: records the reports, answers OK."""

    def __init__(self):
        self.raw: list[bytes] = []

    def query(self, report: bytes) -> RazerResponse:
        self.raw.append(bytes(report))
        r = parse_report(report)
        return RazerResponse(STATUS_OK, r.cmd_class, r.cmd_id, r.size, bytes(r.size))


def _jsonable(v: Any) -> Any:
    if isinstance(v, tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


class DeviceSession:
    """Read table and light plans of one connected device behind a GuardTransport."""

    def __init__(self, pid: int, transport: Transport):
        self.pid = pid
        self._t = GuardTransport(transport)
        base = db.by_pid_any(pid)
        self.info = base.for_pid(pid) if base is not None else None
        self.kind = self.info.kind if self.info is not None else "unknown"
        self._reads: dict[str, Callable[[], Any]] = {}
        self._light: dict[tuple[str, str], list[bytes]] = {}
        if self.info is None:
            self._reads["firmware"] = self._unknown_firmware
        elif self.kind == "mouse":
            self._build_mouse()
        else:
            self._build_other()

    @property
    def reads(self) -> list[str]:
        return list(self._reads)

    @property
    def light_zones(self) -> list[str]:
        return list(dict.fromkeys(z for z, _ in self._light))

    def light_packets(self, zone: str, state: str) -> list[bytes]:
        return list(self._light[(zone, state)])

    # -- building --------------------------------------------------------------------

    def _build_mouse(self) -> None:
        mouse = make_mouse(self.info, self._t)
        for name in MOUSE_READS:
            if mouse.supports(name):
                self._reads[name] = getattr(mouse, name)
        for zone in mouse.brightness_zones():
            self._reads[f"brightness:{zone}"] = lambda z=zone: mouse.brightness(z)
        for zone in dict.fromkeys([*self.info.zones, *mouse.brightness_zones()]):
            plan = {}
            for state, lz in (("green", LightingZone("static", GREEN, 255)),
                              ("off", LightingZone("off", BLACK, 255))):
                cap = _Capture()
                try:
                    make_mouse(self.info, cap).set_effect(zone, lz, store=NOSTORE)
                except (UnsupportedFeature, ValueError, KeyError, TypeError):
                    break
                if not cap.raw or not all(is_allowed_light(p) for p in cap.raw):
                    break
                plan[state] = cap.raw
            else:
                for state, packets in plan.items():
                    self._light[(zone, state)] = packets

    def _build_other(self) -> None:
        for key, cmd in self.info.protocol.items():
            base, _, zone = key.partition(":")
            if base in _OTHER_READS and not zone:
                name = _OTHER_READS[base]
            elif base == "get_brightness" and zone:
                name = f"brightness:{zone}"
            else:
                continue
            if (cmd.steps != (cmd.fn,) or cmd.fn not in _OTHER_GET_FNS
                    or not all(isinstance(a, int) for a in cmd.args)):
                continue
            self._reads[name] = lambda c=cmd, n=name: self._db_get(c, n)
        found = db.light_test_command(self.info)
        if found is None or "matrix" not in self.info.zones:
            return
        cmd, args = found
        if cmd.fn not in C.BUILDERS:
            return
        for state, rgb in (("green", GREEN), ("off", BLACK)):
            built = C.BUILDERS[cmd.fn](*[rgb if a == "*" else a for a in args])
            cls, cid, data, size = built
            packet = build_report(cls, cid, data, size=size, txn=cmd.txn)
            if not is_allowed_light(packet):
                self._light = {k: v for k, v in self._light.items() if k[0] != "matrix"}
                return
            self._light[("matrix", state)] = [packet]

    # -- reads ---------------------------------------------------------------------------

    def _db_get(self, cmd: db.Command, name: str) -> Any:
        cls, cid, data, size = C.BUILDERS[cmd.fn](*cmd.args)
        value = C.PARSERS[cmd.fn](self._t.query(build_report(cls, cid, data, size=size, txn=cmd.txn)).args)
        if name == "battery":
            return round(value * 100 / 255)
        if name == "charging":
            return value != 0
        return value

    def _unknown_firmware(self) -> Any:
        cls, cid, data, size = C.razer_chroma_standard_get_firmware_version()
        for txn in UNKNOWN_FIRMWARE_TXNS:
            try:
                resp = self._t.query(build_report(cls, cid, data, size=size, txn=txn))
            except (RazerError, OSError):
                continue
            self._unknown_txn = txn
            return C.PARSERS["razer_chroma_standard_get_firmware_version"](resp.args)
        raise RazerError("no answer")

    def read(self, name: str) -> dict:
        fn = self._reads[name]
        self._t.responses = []
        self._unknown_txn = None
        try:
            value = fn()
        except (*_READ_ERRORS, BlockedCommand):
            return {"name": name, "status": "failed"}
        if name == "dpi_stages" and isinstance(value, tuple) and len(value) == 2:
            value = {"active": value[0], "stages": _jsonable(value[1])}
        out = {"name": name, "status": "ok", "value": _jsonable(value),
               "raw": b"".join(self._t.responses).hex()}
        if self._unknown_txn is not None:
            out["txn"] = self._unknown_txn
        return out

    def light(self, zone: str, state: str) -> dict:
        packets = self._light[(zone, state)]
        try:
            with self._t.light_mode():
                for p in packets:
                    self._t.query(p)
        except (RazerError, OSError, BlockedCommand):
            return {"zone": zone, "state": state, "status": "failed"}
        return {"zone": zone, "state": state, "status": "ok"}
