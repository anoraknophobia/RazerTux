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

    def open(self, pid: int, interfaces: list[int] | None = None) -> dict: ...
    def read(self, name: str) -> dict: ...
    def light(self, zone: str, state: str) -> dict: ...
    def listen(self, seconds: int, on_signal: Callable[[list], None]) -> dict: ...
    def request_stop(self) -> None: ...
    def close(self) -> None: ...


def _open_req(pid: int, interfaces: list[int] | None) -> dict:
    req = {"op": "open", "pid": pid}
    if interfaces is not None:
        req["interfaces"] = list(interfaces)
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
        self.notice = notice

    def _request(self, req: dict, on_signal=None) -> dict:
        out: list[dict] = []

        def emit(d: dict) -> None:
            if "signal" in d and on_signal is not None:
                on_signal(d["signal"])
            else:
                out.append(d)

        def poll_input(timeout: float):
            return json.dumps({"op": "stop"}) if self._stop.is_set() else None

        self._helper.handle(json.dumps(req), emit, poll_input)
        final = out[-1]
        if "error" in final:
            raise BackendError(final["error"])
        return final

    def open(self, pid, interfaces=None):
        return self._request(_open_req(pid, interfaces))

    def read(self, name):
        return self._request({"op": "read", "name": name})

    def light(self, zone, state):
        return self._request({"op": "light", "zone": zone, "state": state})

    def listen(self, seconds, on_signal):
        self._stop.clear()
        return self._request({"op": "listen", "seconds": seconds}, on_signal)

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
        self._write(req)
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise BackendError("record helper exited")
            d = json.loads(line)
            if "signal" in d:
                if on_signal is not None:
                    on_signal(d["signal"])
                continue
            if "error" in d:
                raise BackendError(d["error"])
            return d

    def open(self, pid, interfaces=None):
        return self._request(_open_req(pid, interfaces))

    def read(self, name):
        return self._request({"op": "read", "name": name})

    def light(self, zone, state):
        return self._request({"op": "light", "zone": zone, "state": state})

    def listen(self, seconds, on_signal):
        return self._request({"op": "listen", "seconds": seconds}, on_signal)

    def request_stop(self):
        if self._proc is not None:
            self._write({"op": "stop"})

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
