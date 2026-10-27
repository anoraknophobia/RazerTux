"""Privilege-free discovery of Razer USB devices from sysfs (pure file reads: no ioctls, no writes).

Serial numbers are never read: the USB ``serial`` attribute and the evdev ``uniq``/``phys`` files are not opened.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from evdev import ecodes as E

from razertux.devices import db

RAZER_VID = 0x1532
_HEX = re.compile(r"^[0-9a-fA-F]+$")


@dataclass
class Node:
    path: str  # /dev/hidrawN or /dev/input/eventK
    readable: bool = False
    writable: bool = False  # only checked for hidraw


@dataclass
class EventNode(Node):
    name: str = ""
    ev: set[int] = field(default_factory=set)
    key: set[int] = field(default_factory=set)
    rel: set[int] = field(default_factory=set)
    abs: set[int] = field(default_factory=set)
    msc: set[int] = field(default_factory=set)


@dataclass
class FoundInterface:
    number: int
    cls: int
    subclass: int
    protocol: int
    descriptor: bytes = b""
    hidraw: list[Node] = field(default_factory=list)
    events: list[EventNode] = field(default_factory=list)


@dataclass
class FoundDevice:
    vid: int
    pid: int
    bcd_device: str
    manufacturer: str
    product: str
    interfaces: list[FoundInterface] = field(default_factory=list)
    bus: str = ""  # sysfs USB device name ("5-1.2"): tells identical units apart, never reported

    @property
    def accessible(self) -> bool:
        """True when every hidraw node is readable+writable and every event node is readable."""
        nodes_ok = all(n.readable and n.writable for i in self.interfaces for n in i.hidraw)
        events_ok = all(e.readable for i in self.interfaces for e in i.events)
        return nodes_ok and events_ok


def _text(p: Path) -> str:
    try:
        return p.read_text(errors="replace").strip()
    except OSError:
        return ""


def _int(p: Path, base: int) -> int | None:
    try:
        return int(_text(p), base)
    except ValueError:
        return None


def parse_bitmap(text: str) -> set[int]:
    """Parse a sysfs capability bitmap (space separated hex words, most significant first, 64-bit longs)."""
    words = text.split()
    if not words or not all(_HEX.match(w) for w in words):
        return set()
    bits = set()
    for idx, w in enumerate(reversed(words)):
        v = int(w, 16)
        for b in range(v.bit_length()):
            if v >> b & 1:
                bits.add(idx * 64 + b)
    return bits


def _event_node(inp: Path, ev_dir: Path) -> EventNode:
    node = EventNode(path="/dev/input/" + ev_dir.name, name=_text(inp / "name"))
    node.readable = os.access(node.path, os.R_OK)
    cap = inp / "capabilities"
    for attr in ("ev", "key", "rel", "abs", "msc"):
        setattr(node, attr, parse_bitmap(_text(cap / attr)))
    return node


def _glob(p: Path, pattern: str) -> list[Path]:
    try:
        return sorted(p.glob(pattern))
    except OSError:  # directory vanished (hot-unplug) between listing and reading
        return []


def _interface(idir: Path) -> FoundInterface | None:
    num, cls = _int(idir / "bInterfaceNumber", 16), _int(idir / "bInterfaceClass", 16)
    if num is None or cls is None:
        return None
    fi = FoundInterface(num, cls, _int(idir / "bInterfaceSubClass", 16) or 0,
                        _int(idir / "bInterfaceProtocol", 16) or 0)
    for hid in _glob(idir, "0003:*"):
        try:
            fi.descriptor = (hid / "report_descriptor").read_bytes()
        except OSError:
            pass
        for h in _glob(hid / "hidraw", "hidraw*"):
            path = "/dev/" + h.name
            rw = os.access(path, os.R_OK | os.W_OK)
            fi.hidraw.append(Node(path, rw, rw))
        for inp in _glob(hid / "input", "input*"):
            for ev_dir in _glob(inp, "event*"):
                fi.events.append(_event_node(inp, ev_dir))
    return fi


def list_razer_devices(sysfs_root: str = "/sys") -> list[FoundDevice]:
    base = Path(sysfs_root) / "bus/usb/devices"
    found: list[tuple[int, str, FoundDevice]] = []
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []
    for d in entries:
        if ":" in d.name or _int(d / "idVendor", 16) != RAZER_VID:
            continue
        pid = _int(d / "idProduct", 16)
        if pid is None:
            continue
        dev = FoundDevice(RAZER_VID, pid, _text(d / "bcdDevice"), _text(d / "manufacturer"), _text(d / "product"),
                          bus=d.name)
        for idir in _glob(d, d.name + ":*"):
            fi = _interface(idir)
            if fi is not None:
                dev.interfaces.append(fi)
        dev.interfaces.sort(key=lambda i: i.number)
        found.append((pid, d.name, dev))
    found.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in found]


def report_device(dev: FoundDevice) -> dict:
    """The ``device`` section for razertux.recorder.report.Report (connection is left to the caller)."""
    info = db.by_pid_any(dev.pid)
    return {"vid": dev.vid, "pid": dev.pid, "usb_manufacturer": dev.manufacturer, "usb_product": dev.product,
            "bcdDevice": dev.bcd_device, "kind": db.kind_by_pid(dev.pid) or "unknown",
            "db_model": info.model if info else "", "connection": info.pids.get(dev.pid, "") if info else ""}


def _code_name(code: int, *tables: dict) -> str:
    for table in tables:
        n = table.get(code)
        if isinstance(n, (list, tuple)):
            n = n[0] if n else None
        if n:
            return str(n)
    return f"CODE_{code}"


def _event_kind(e: EventNode) -> str:
    n = e.name.lower()
    if E.KEY_A in e.key or "keyboard" in n:
        return "keyboard"
    if "consumer" in n:
        return "consumer"
    if "system" in n:
        return "system"
    if e.rel:
        return "mouse"
    if e.key or e.abs:
        return "other"
    return "misc"


def _ranges(codes) -> str:
    """Sorted codes as compact "1-83,85,90-92" text."""
    out, start, prev = [], None, None
    for c in sorted(codes) + [None]:
        if start is None:
            start = prev = c
        elif c is not None and c == prev + 1:
            prev = c
        else:
            out.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = c
    return ",".join(out)


def _event_names(e: EventNode) -> list[str]:
    """Named non-keyboard-key capabilities: buttons (BTN_*), relative and absolute axes."""
    names = [_code_name(c, E.BTN, E.KEY) for c in sorted(e.key) if c >= E.BTN_MISC]
    names += [_code_name(c, E.REL) for c in sorted(e.rel)]
    names += [_code_name(c, E.ABS) for c in sorted(e.abs)]
    return names


def report_interfaces(dev: FoundDevice) -> list[dict]:
    """The ``interfaces`` section: class triple, HID descriptor (hex + sha1), evdev names and key code ranges."""
    out = []
    for i in dev.interfaces:
        evdev: dict[str, list[str]] = {}
        keys: dict[str, set[int]] = {}
        for e in i.events:
            k = _event_kind(e)
            evdev.setdefault(k, []).extend(_event_names(e))
            keys.setdefault(k, set()).update(c for c in e.key if c < E.BTN_MISC)
        entry = {"number": i.number, "class": i.cls, "subclass": i.subclass, "protocol": i.protocol,
                 "evdev": {k: sorted(set(v)) for k, v in sorted(evdev.items())},
                 "evdev_keys": {k: _ranges(v) for k, v in sorted(keys.items()) if v}}
        if i.descriptor:
            entry["hid_descriptor_hex"] = i.descriptor.hex()
            entry["descriptor_sha1"] = hashlib.sha1(i.descriptor).hexdigest()
        out.append(entry)
    return out
