"""Recorder backends: the same operations without a password (in-process, only nodes the user can
already open) or with a password (the pkexec record helper, see ``helper``)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from typing import Protocol

from razertux.recorder import helper as _helper
from razertux.recorder.discover import EventNode, Node

HELPER_PATH = "/usr/lib/razertux/razertux-record-helper"
POLICY_PATH = "/usr/share/polkit-1/actions/io.github.razertux.record.policy"
UNAVAILABLE = "Password mode unavailable: {why}. Recording continues with the devices you can already access."


class BackendError(Exception):
    pass


class Backend(Protocol):
    notice: str

    def open(self, pid: int, interfaces: list[int] | None = None, bus: str | None = None) -> dict: ...
    def read(self, name: str) -> dict: ...
    def light(self, zone: str, state: str) -> dict: ...
    def listen(self, seconds: int, on_signal: Callable[[list], None], kind: str | None = None) -> dict: ...
    def request_stop(self) -> None: ...
    def close(self) -> None: ...


def _listen_req(seconds: int, kind: str | None) -> dict:
    req = {"op": "listen", "seconds": seconds}
    if kind is not None:
        req["kind"] = kind  # only used by the helper when the database does not know the device
    return req


def _open_req(pid: int, interfaces: list[int] | None, bus: str | None = None) -> dict:
    req = {"op": "open", "pid": pid}
    if interfaces is not None:
        req["interfaces"] = list(interfaces)
    if bus:
        req["bus"] = bus  # the unit chosen in the wizard (sysfs USB device name)
    return req


def user_can_open(node: Node) -> bool:
    if isinstance(node, EventNode):
        return node.readable
    return node.readable and node.writable


class InProcessBackend:
    """No password: the helper logic in this process, restricted to accessible nodes."""

    def __init__(self, helper_obj: _helper.Helper | None = None, sysfs_root: str = "/sys", notice: str = ""):
        self._helper = helper_obj or _helper.Helper(sysfs_root=sysfs_root, node_allowed=user_can_open)
        self._stop = threading.Event()
        self._next_id = 0
        self.notice = notice

    def _request(self, req: dict, on_signal=None) -> dict:
        self._next_id += 1
        rid = self._next_id
        out: list[dict] = []

        def emit(d: dict) -> None:
            if d.get("id") != rid:
                return  # not an answer to this request
            if "signal" in d:
                if on_signal is not None:
                    on_signal(d["signal"])
            else:
                out.append(d)

        def poll_input(timeout: float):
            if not self._stop.is_set():
                return None
            self._stop.clear()
            return json.dumps({"op": "stop", "id": rid})

        self._helper.handle(json.dumps({**req, "id": rid}), emit, poll_input)
        if not out:
            raise BackendError("no answer")
        final = out[-1]
        if "error" in final:
            raise BackendError(final["error"])
        return final

    def open(self, pid, interfaces=None, bus=None):
        return self._request(_open_req(pid, interfaces, bus))

    def read(self, name):
        return self._request({"op": "read", "name": name})

    def light(self, zone, state):
        return self._request({"op": "light", "zone": zone, "state": state})

    def listen(self, seconds, on_signal, kind=None):
        self._stop.clear()
        return self._request(_listen_req(seconds, kind), on_signal)

    def request_stop(self):
        self._stop.set()

    def close(self):
        self._helper.close()


def _popen(argv: list[str]):
    return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            bufsize=1, close_fds=True)


class HelperBackend:
    """Password mode: ``pkexec /usr/lib/razertux/razertux-record-helper`` over JSON lines."""

    notice = ""

    def __init__(self, spawn: Callable[[list[str]], object] = _popen):
        self._spawn = spawn
        self._proc = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._listen_id: int | None = None

    @staticmethod
    def availability(which=shutil.which, exists=os.path.exists) -> tuple[bool, str]:
        if which("pkexec") is None:
            return False, UNAVAILABLE.format(why="pkexec is not installed")
        if not exists(HELPER_PATH):
            return False, UNAVAILABLE.format(why="the record helper is not installed (source checkout?)")
        if not exists(POLICY_PATH):
            return False, UNAVAILABLE.format(why="the polkit policy is not installed (source checkout?)")
        return True, ""

    def start(self) -> None:
        self._proc = self._spawn(["pkexec", HELPER_PATH])
        line = self._proc.stdout.readline()
        if not line:
            code = self._proc.wait()
            self._proc = None
            raise BackendError(f"authorization cancelled or denied (pkexec exit {code})")
        try:
            hello = json.loads(line)
        except ValueError:
            hello = None
        if not isinstance(hello, dict) or hello.get("ready") is not True:
            self.close()
            raise BackendError("unexpected answer from the record helper")

    def _write(self, req: dict) -> None:
        with self._lock:
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()

    def _request(self, req: dict, on_signal=None) -> dict:
        if self._proc is None:
            raise BackendError("record helper not running")
        self._next_id += 1
        rid = self._next_id
        if req["op"] == "listen":
            self._listen_id = rid
        try:
            self._write({**req, "id": rid})
            return self._answer(rid, on_signal)
        finally:
            self._listen_id = None

    def _answer(self, rid: int, on_signal) -> dict:
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise BackendError("record helper exited")
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("id") != rid:
                continue  # stale line of an earlier request (e.g. an error about a late stop)
            if "signal" in d:
                if on_signal is not None:
                    on_signal(d["signal"])
                continue
            if "error" in d:
                raise BackendError(d["error"])
            return d

    def open(self, pid, interfaces=None, bus=None):
        return self._request(_open_req(pid, interfaces, bus))

    def read(self, name):
        return self._request({"op": "read", "name": name})

    def light(self, zone, state):
        return self._request({"op": "light", "zone": zone, "state": state})

    def listen(self, seconds, on_signal, kind=None):
        return self._request(_listen_req(seconds, kind), on_signal)

    def request_stop(self):
        """Ends a running listen early (callable from another thread). The stop carries the
        listen's id; a stop that arrives after the listen ended is ignored by the helper."""
        rid = self._listen_id
        if self._proc is not None and rid is not None:
            self._write({"op": "stop", "id": rid})

    def close(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            with self._lock:
                proc.stdin.write(json.dumps({"op": "quit"}) + "\n")
                proc.stdin.flush()
                proc.stdin.close()
            proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            proc.kill()


def choose_backend(password: bool, sysfs_root: str = "/sys") -> Backend:
    """HelperBackend (not started yet) when password mode is wanted and installed, else
    InProcessBackend; ``notice`` then says why password mode is unavailable."""
    if password:
        ok, reason = HelperBackend.availability()
        if ok:
            return HelperBackend()
        return InProcessBackend(sysfs_root=sysfs_root, notice=reason)
    return InProcessBackend(sysfs_root=sysfs_root)
