"""razertux-record-helper: the privileged part of the device recorder (run as root via pkexec).

Protocol: JSON lines on stdin/stdout. The helper first writes ``{"ready": true, "protocol": 1}``.
Requests (no other fields are accepted, every other request gets ``{"error": "..."}``):

- ``{"op": "open", "pid": int, "interfaces": [int, ...]}`` (interfaces optional, default all):
  open the connected vendor-0x1532 device with this PID. Its nodes come only from sysfs discovery
  (``discover.list_razer_devices``); the client never names a path. One device per helper run.
  -> ``{"ok": true, "pid", "kind", "model", "control", "reads": [...], "light_zones": [...],
  "listen": [source, ...]}``
- ``{"op": "read", "name": str}``: a name from the open device's ``reads`` table
  -> ``{"ok": true, "name", "status": "ok"|"failed", "value"?, "raw"?, "txn"?}``
- ``{"op": "light", "zone": str, "state": "green"|"off"}``: a zone from ``light_zones``
  -> ``{"ok": true, "zone", "state", "status": "ok"|"failed"}``
- ``{"op": "listen", "seconds": int 1..120}``: streams ``{"signal": [source, ev_type, code]}``
  (keylogger filter applied here, see ``keyfilter``) and ends with
  ``{"done": true, "standard_keys_seen": int}``. While listening only ``{"op": "stop"}`` is
  accepted (ends early); stdin EOF ends the listen and the helper.
- ``{"op": "quit"}`` -> ``{"ok": true}`` and exit.

The helper exits on stdin EOF and after ``IDLE_TIMEOUT`` seconds without a request. Lines longer
than ``MAX_LINE`` bytes are rejected. No shell, no eval, no client bytes ever reach a device:
everything sent is built from the device database (``whitelist``) and checked by its guard.
"""
from __future__ import annotations

import json
import os
import re
import select
import stat
import struct
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from razertux.devices import db
from razertux.razer.transport import HidrawTransport, _FcntlIo
from razertux.recorder.discover import RAZER_VID, Node, _event_kind, list_razer_devices
from razertux.recorder.keyfilter import SignalFilter
from razertux.recorder.whitelist import LIGHT_STATES, DeviceSession

PROTOCOL = 1
MAX_LINE = 4096
IDLE_TIMEOUT = 600
LISTEN_MAX = 120
POLL_STEP = 0.25
MAX_IFACES = 16

_HIDRAW_RE = re.compile(r"/dev/(hidraw\d{1,4})")
_EVENT_RE = re.compile(r"/dev/input/(event\d{1,4})")

_FIELDS = {
    "open": ({"op", "pid", "interfaces"}, {"op", "pid"}),
    "read": ({"op", "name"}, {"op", "name"}),
    "light": ({"op", "zone", "state"}, {"op", "zone", "state"}),
    "listen": ({"op", "seconds"}, {"op", "seconds"}),
    "quit": ({"op"}, {"op"}),
}

Emit = Callable[[dict], None]
PollInput = Callable[[float], "str | None"]


# -- node ownership and the real openers -----------------------------------------------------

def node_owner(sysfs_root: str, path: str) -> tuple[int, int, int] | None:
    """(vid, pid, rdev) of the USB device behind /dev/hidrawN or /dev/input/eventN, from sysfs."""
    if not isinstance(path, str):
        return None
    m = _HIDRAW_RE.fullmatch(path)
    cls = "hidraw"
    if m is None:
        m, cls = _EVENT_RE.fullmatch(path), "input"
    if m is None:
        return None
    root = Path(sysfs_root).resolve()
    try:
        real = (root / "class" / cls / m.group(1)).resolve(strict=True)
        major, minor = (int(x) for x in (real / "dev").read_text().strip().split(":"))
    except (OSError, ValueError):
        return None
    if root not in real.parents:
        return None
    p = real.parent
    while p != root and root in p.parents:
        try:
            vid = int((p / "idVendor").read_text().strip(), 16)
            pid = int((p / "idProduct").read_text().strip(), 16)
            return vid, pid, os.makedev(major, minor)
        except (OSError, ValueError):
            p = p.parent
    return None


def _open_owned(path: str, pid: int, sysfs_root: str, flags: int) -> int:
    """Open a device node only if sysfs says it belongs to the Razer device ``pid`` (checked
    before and after opening, against the opened inode's device number)."""
    before = node_owner(sysfs_root, path)
    if before is None or before[:2] != (RAZER_VID, pid):
        raise PermissionError("node does not belong to the device")
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        after = node_owner(sysfs_root, path)
        if not stat.S_ISCHR(st.st_mode) or after != before or st.st_rdev != before[2]:
            raise PermissionError("node changed while opening")
    except BaseException:
        os.close(fd)
        raise
    return fd


class _FdHidrawTransport(HidrawTransport):
    def __init__(self, fd: int, path: str):  # noqa: super().__init__ would reopen by path
        self.path = path
        self._io = _FcntlIo()
        self._fd = fd


def open_verified_hidraw(path: str, pid: int, sysfs_root: str = "/sys") -> HidrawTransport:
    return _FdHidrawTransport(_open_owned(path, pid, sysfs_root, os.O_RDWR), path)


_EVENT = struct.Struct("@llHHi")  # struct input_event


class EvdevSource:
    """Raw input_event reader over several event nodes (read only, never grabbed)."""

    def __init__(self, fds: dict[int, str]):
        self._fds = fds

    def poll(self, timeout: float) -> list[tuple[str, int, int, int]]:
        if not self._fds:
            time.sleep(timeout)
            return []
        ready, _, _ = select.select(list(self._fds), [], [], timeout)
        out = []
        for fd in ready:
            try:
                data = os.read(fd, _EVENT.size * 64)
            except BlockingIOError:
                continue
            except OSError:  # unplugged
                os.close(fd)
                self._fds.pop(fd, None)
                continue
            for off in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
                _, _, t, c, v = _EVENT.unpack_from(data, off)
                out.append((self._fds[fd], t, c, v))
        return out

    def close(self) -> None:
        for fd in self._fds:
            os.close(fd)
        self._fds = {}


def open_verified_events(nodes: list[tuple[str, str]], pid: int, sysfs_root: str = "/sys") -> EvdevSource:
    fds: dict[int, str] = {}
    try:
        for path, label in nodes:
            fds[_open_owned(path, pid, sysfs_root, os.O_RDONLY | os.O_NONBLOCK)] = label
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise
    return EvdevSource(fds)


# -- the helper -------------------------------------------------------------------------------

@dataclass
class _Open:
    pid: int
    kind: str
    session: DeviceSession | None
    transport: object | None
    events: list[tuple[str, str]]


def _reject_constant(name: str):
    raise ValueError(name)


def _is_int(v, lo: int, hi: int) -> bool:
    return type(v) is int and lo <= v <= hi


class Helper:
    def __init__(self, sysfs_root: str = "/sys", open_hidraw=None, open_events=None,
                 clock: Callable[[], float] = time.monotonic,
                 node_allowed: Callable[[Node], bool] | None = None):
        self.root = sysfs_root
        self._open_hidraw = open_hidraw or (lambda p, pid: open_verified_hidraw(p, pid, self.root))
        self._open_events = open_events or (lambda n, pid: open_verified_events(n, pid, self.root))
        self._clock = clock
        self._allowed = node_allowed or (lambda node: True)
        self._dev: _Open | None = None

    # -- entry -------------------------------------------------------------------------

    def handle(self, line: str, emit: Emit, poll_input: PollInput | None = None) -> bool:
        """Handle one request line; False when the helper must exit."""
        req = self._parse(line, emit)
        if req is None:
            return True
        op = req["op"]
        try:
            if op == "quit":
                emit({"ok": True})
                return False
            if op == "open":
                self._op_open(req, emit)
                return True
            if self._dev is None:
                emit({"error": "no device open"})
                return True
            if op == "read":
                self._op_read(req, emit)
            elif op == "light":
                self._op_light(req, emit)
            elif op == "listen":
                return self._op_listen(req, emit, poll_input)
        except Exception:  # never leak details, never die on a bad device answer
            emit({"error": "internal error"})
        return True

    def _parse(self, line, emit: Emit) -> dict | None:
        if not isinstance(line, str) or len(line.encode("utf-8", "replace")) > MAX_LINE:
            emit({"error": "request too long"})
            return None
        try:
            req = json.loads(line, parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            emit({"error": "invalid JSON"})
            return None
        if type(req) is not dict or type(req.get("op")) is not str or req["op"] not in _FIELDS:
            emit({"error": "unknown op"})
            return None
        allowed, required = _FIELDS[req["op"]]
        if not set(req) <= allowed or not required <= set(req):
            emit({"error": "unexpected or missing field"})
            return None
        return req

    def close(self) -> None:
        if self._dev is not None and hasattr(self._dev.transport, "close"):
            try:
                self._dev.transport.close()
            except OSError:
                pass
        self._dev = None

    # -- ops ---------------------------------------------------------------------------

    def _op_open(self, req: dict, emit: Emit) -> None:
        pid, ifaces = req["pid"], req.get("interfaces")
        if not _is_int(pid, 1, 0xFFFF):
            return emit({"error": "invalid pid"})
        if ifaces is not None and (type(ifaces) is not list or len(ifaces) > MAX_IFACES
                                   or not all(_is_int(i, 0, 255) for i in ifaces)):
            return emit({"error": "invalid interfaces"})
        if self._dev is not None:
            return emit({"error": "a device is already open"})
        devs = [d for d in list_razer_devices(self.root) if d.vid == RAZER_VID and d.pid == pid]
        if not devs:
            return emit({"error": "device not connected"})
        dev = devs[0]
        numbers = {i.number for i in dev.interfaces}
        sel = set(ifaces) if ifaces is not None else numbers
        if not sel <= numbers:
            return emit({"error": "unknown interface"})
        info = db.by_pid_any(pid)
        control_if = info.interface if info is not None else 0
        transport = None
        if control_if in sel:
            iface = next(i for i in dev.interfaces if i.number == control_if)
            nodes = [n for n in iface.hidraw if _HIDRAW_RE.fullmatch(n.path) and self._allowed(n)]
            if nodes:
                try:
                    transport = self._open_hidraw(nodes[0].path, pid)
                except OSError:
                    transport = None
        session = DeviceSession(pid, transport) if transport is not None else None
        events = [(e.path, f"if{i.number}:{_event_kind(e)}") for i in dev.interfaces if i.number in sel
                  for e in i.events if _EVENT_RE.fullmatch(e.path) and self._allowed(e)]
        kind = db.kind_by_pid(pid) or "unknown"
        self._dev = _Open(pid, kind, session, transport, events)
        emit({"ok": True, "pid": pid, "kind": kind, "model": info.model if info else "",
              "control": session is not None,
              "reads": session.reads if session else [],
              "light_zones": session.light_zones if session else [],
              "listen": list(dict.fromkeys(label for _, label in events))})

    def _op_read(self, req: dict, emit: Emit) -> None:
        s = self._dev.session
        name = req["name"]
        if s is None or type(name) is not str or name not in s.reads:
            return emit({"error": "unknown read"})
        emit({"ok": True, **s.read(name)})

    def _op_light(self, req: dict, emit: Emit) -> None:
        s = self._dev.session
        zone, state = req["zone"], req["state"]
        if (s is None or type(zone) is not str or zone not in s.light_zones
                or type(state) is not str or state not in LIGHT_STATES):
            return emit({"error": "light not allowed"})
        emit({"ok": True, **s.light(zone, state)})

    def _op_listen(self, req: dict, emit: Emit, poll_input: PollInput | None) -> bool:
        seconds = req["seconds"]
        if not _is_int(seconds, 1, LISTEN_MAX):
            emit({"error": f"seconds must be 1-{LISTEN_MAX}"})
            return True
        if not self._dev.events:
            emit({"error": "no input nodes"})
            return True
        try:
            src = self._open_events(list(self._dev.events), self._dev.pid)
        except OSError:
            emit({"error": "cannot open input nodes"})
            return True
        filt = SignalFilter(self._dev.kind)
        deadline = self._clock() + seconds
        keep = True
        try:
            while (rem := deadline - self._clock()) > 0:
                for source, t, c, v in src.poll(min(rem, POLL_STEP)):
                    sig = filt.feed(source, t, c, v)
                    if sig is not None:
                        emit({"signal": list(sig)})
                if poll_input is None:
                    continue
                x = poll_input(0)
                if x is None:
                    continue
                if x == "":
                    keep = False
                    break
                if _is_stop(x):
                    break
                emit({"error": "listening: only stop is accepted"})
        finally:
            src.close()
        emit({"done": True, "standard_keys_seen": filt.standard_keys_seen})
        return keep


def _is_stop(line: str) -> bool:
    if len(line) > MAX_LINE:
        return False
    try:
        return json.loads(line, parse_constant=_reject_constant) == {"op": "stop"}
    except (ValueError, RecursionError):
        return False


# -- stdin loop ---------------------------------------------------------------------------------

EOF = object()
OVERSIZE = object()


class LineReader:
    """Line reader on a raw fd with a timeout; overlong lines are discarded as OVERSIZE."""

    def __init__(self, fd: int):
        self.fd = fd
        self.buf = b""
        self.discarding = False
        self.eof = False

    def readline(self, timeout: float):
        deadline = time.monotonic() + timeout
        while True:
            nl = self.buf.find(b"\n")
            if nl >= 0:
                line, self.buf = self.buf[:nl], self.buf[nl + 1:]
                if self.discarding or len(line) > MAX_LINE:
                    self.discarding = False
                    return OVERSIZE
                return line.decode("utf-8", "replace")
            if len(self.buf) > MAX_LINE:
                self.buf = b""
                self.discarding = True
            if self.eof:
                return EOF
            ready, _, _ = select.select([self.fd], [], [], max(deadline - time.monotonic(), 0))
            if not ready:
                return None
            chunk = os.read(self.fd, 65536)
            if not chunk:
                self.eof = True
            self.buf += chunk


def serve(h: Helper, fd: int, write: Callable[[str], None], idle_timeout: float = IDLE_TIMEOUT) -> int:
    reader = LineReader(fd)

    def emit(d: dict) -> None:
        write(json.dumps(d, separators=(",", ":")))

    def poll_input(timeout: float):
        x = reader.readline(timeout)
        if x is EOF:
            return ""
        if x is OVERSIZE:
            return "<oversize>"
        return x if x != "" else " "

    emit({"ready": True, "protocol": PROTOCOL})
    try:
        while True:
            line = reader.readline(idle_timeout)
            if line is None:
                emit({"error": "idle timeout"})
                return 0
            if line is EOF:
                return 0
            if line is OVERSIZE:
                emit({"error": "request too long"})
                continue
            if not h.handle(line, emit, poll_input):
                return 0
    finally:
        h.close()


def main() -> int:
    os.umask(0o077)
    out = sys.stdout.fileno()

    def write(s: str) -> None:
        data = (s + "\n").encode()
        while data:
            data = data[os.write(out, data):]

    return serve(Helper(), sys.stdin.fileno(), write)


if __name__ == "__main__":
    sys.exit(main())
