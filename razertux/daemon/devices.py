"""Naga input devices: discovery/hotplug (udev), grab + routing, vendor hidraw."""
from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

import pyudev
from evdev import InputDevice
from evdev import ecodes as E

from razertux.engine.output import Output
from razertux.hw.buttons import ButtonId, identify, identify_vendor

from .uinput_out import VIRTUAL_NAME, VIRTUAL_NAMES

logger = logging.getLogger(__name__)

__all__ = ["VIRTUAL_NAME", "VIRTUAL_NAMES", "DeviceWatcher", "HidrawReader", "InputRouter",
           "classify", "source_from_links"]

NAGA_VENDOR = RAZER_VENDOR = "1532"
NAGA_PIDS = {"00a7", "00a8"}
_NAGA_PID_VALUES = {0x00A7, 0x00A8}  # until the daemon passes the full DB PID set
# Opening right at login can fail until logind/udev applied the seat ACL (EACCES/EPERM),
# or while another program still holds the grab (EBUSY): those are retried.
_RETRY_ERRNOS = {errno.EACCES, errno.EPERM, errno.EBUSY}
# Wheel axes are produced by the mapper itself (WHEEL_* buttons), never forwarded.
_WHEEL_AXES = {E.REL_WHEEL, E.REL_HWHEEL, E.REL_WHEEL_HI_RES, E.REL_HWHEEL_HI_RES}


_LINK_RE = re.compile(r"-(?:if(?P<n>\d+)-)?event-(?P<kind>mouse|kbd)$|-event-if(?P<ev>\d+)$")


def source_from_links(links: Iterable[str]) -> str | None:
    """Source name from a /dev/input/by-id link suffix, or None.

    event-mouse -> mouse, ifNN-event-kbd -> ifNN, ifNN-event-mouse -> ifNN_mouse,
    event-ifNN -> ifNN_event."""
    for link in links:
        if "/by-id/" not in link:
            continue
        m = _LINK_RE.search(link)
        if not m:
            continue
        if m["ev"]:
            return f"if{m['ev']}_event"
        n, kind = m["n"], m["kind"]
        if kind == "mouse":
            return f"if{n}_mouse" if n else "mouse"
        if n:
            return f"if{n}"
    return None


def classify(props: Mapping[str, str], known_pids: set[int]) -> tuple[int, str] | None:
    """(pid, source) for a udev input event node of a known Razer device, else None."""
    if not props.get("DEVNAME", "").startswith("/dev/input/event"):
        return None
    if props.get("ID_VENDOR_ID", "").lower() != RAZER_VENDOR:
        return None
    try:
        pid = int(props.get("ID_MODEL_ID", ""), 16)
    except ValueError:
        return None
    if pid not in known_pids:
        return None
    source = source_from_links(props.get("DEVLINKS", "").split())
    if source:
        return pid, source
    # Fallback (e.g. different by-id names on the wireless dongle).
    iface = props.get("ID_USB_INTERFACE_NUM", "")
    keyboard = props.get("ID_INPUT_KEYBOARD") == "1"
    if iface == "00":
        return (pid, "mouse") if props.get("ID_INPUT_MOUSE") == "1" else None
    if iface.isdigit() and keyboard:
        return pid, f"if{iface}"
    if iface == "01":
        return pid, "if01_event"
    return None


def _list_input_devices():
    return pyudev.Context().list_devices(subsystem="input")


class DeviceWatcher:
    """Reports Naga evdev nodes (initial scan + udev hotplug); never our uinput device.

    on_added(source, dev) may return False when it could not use the device (grab
    failed); like a refused open (EACCES/EPERM/EBUSY) that is retried `retries` times,
    `retry_delay` seconds apart."""

    def __init__(self, on_added: Callable[[str, Any], bool | None],
                 on_removed: Callable[[str], None],
                 *, list_devices: Callable[[], Iterable[Any]] = _list_input_devices,
                 open_device: Callable[[str], Any] = InputDevice,
                 retries: int = 5, retry_delay: float = 1.0,
                 call_later: Callable[..., Any] | None = None) -> None:
        self._on_added = on_added
        self._on_removed = on_removed
        self._list = list_devices
        self._open = open_device
        self._retries = retries
        self._retry_delay = retry_delay
        self._call_later = call_later
        self._stopped = False
        self._known: dict[str, str] = {}
        self._monitor: pyudev.Monitor | None = None

    def scan(self) -> None:
        for udev_dev in self._list():
            self._add(udev_dev)

    def start(self) -> None:
        self._monitor = pyudev.Monitor.from_netlink(pyudev.Context())
        self._monitor.filter_by("input")
        self._monitor.start()
        asyncio.get_running_loop().add_reader(self._monitor.fileno(), self._poll)

    def stop(self) -> None:
        self._stopped = True  # pending retries become no-ops
        if self._monitor is not None:
            asyncio.get_running_loop().remove_reader(self._monitor.fileno())
            self._monitor = None

    def _poll(self) -> None:
        while self._monitor is not None and (d := self._monitor.poll(timeout=0)) is not None:
            self.handle_udev(d)

    def handle_udev(self, udev_dev: Any) -> None:
        try:
            if udev_dev.action == "add":
                self._add(udev_dev)
            elif udev_dev.action == "remove":
                path = udev_dev.device_node
                if path in self._known:
                    del self._known[path]
                    self._on_removed(path)
        except Exception:
            logger.exception("Fehler bei udev-Ereignis")

    def _retry(self, udev_dev: Any, attempt: int) -> bool:
        """Schedule another _add; False once the retries are used up."""
        if attempt >= self._retries:
            return False
        later = self._call_later or asyncio.get_running_loop().call_later
        later(self._retry_delay, self._add, udev_dev, attempt + 1)
        return True

    def _add(self, udev_dev: Any, attempt: int = 0) -> None:
        if self._stopped:
            return
        found = classify(dict(udev_dev.properties), _NAGA_PID_VALUES)
        path = udev_dev.device_node
        if found is None or path in self._known:
            return
        source = found[1]
        try:
            dev = self._open(path)
        except OSError as e:
            if e.errno in _RETRY_ERRNOS and self._retry(udev_dev, attempt):
                logger.info("Kann %s noch nicht öffnen (%s), neuer Versuch …", path, e)
            else:
                logger.warning("Kann %s nicht öffnen: %s", path, e)
            return
        if dev.name in VIRTUAL_NAMES:
            dev.close()
            return
        self._known[path] = source
        logger.info("Naga-Gerät gefunden: %s (%s)", path, source)
        try:
            ok = self._on_added(source, dev)
        except Exception:
            logger.exception("Fehler beim Hinzufügen von %s", path)
            return
        if ok is False:  # not grabbed (e.g. EBUSY): forget it, try again later
            del self._known[path]
            if not self._retry(udev_dev, attempt):
                logger.warning("Gebe %s auf: nicht exklusiv nutzbar", path)


class InputRouter:
    """Grabs Naga devices, feeds button events into the mapper, forwards the rest."""

    def __init__(self, mapper: Any, out: Output) -> None:
        self._mapper = mapper
        self._out = out
        self._devs: dict[str, tuple[Any, asyncio.Task]] = {}
        self._top_down: list[ButtonId] = []
        self._bg: set[asyncio.Task] = set()

    @property
    def paths(self) -> set[str]:
        return set(self._devs)

    def add(self, source: str, dev: Any) -> bool:
        """Grab and start reading; False if the grab failed (device closed)."""
        loop = asyncio.get_running_loop()
        stale = self._devs.pop(dev.path, None)
        if stale:
            # Node path reused before we saw the removal: retire the old device.
            old, task = stale
            task.cancel()
            with contextlib.suppress(OSError):
                old.ungrab()
            old.close()
            # Created before the new reader task, so the release runs first.
            bg = loop.create_task(self._release())
            self._bg.add(bg)
            bg.add_done_callback(self._bg.discard)
        try:
            dev.grab()
        except OSError as e:
            logger.warning("Kann %s nicht exklusiv öffnen (grab): %s", dev.path, e)
            dev.close()
            return False
        task = loop.create_task(self._read(source, dev))
        self._devs[dev.path] = (dev, task)
        return True

    async def remove(self, path: str) -> None:
        """Device unplugged: stop reading, forget it, release everything held."""
        entry = self._devs.pop(path, None)
        if entry:
            dev, task = entry
            await _cancel(task)
            dev.close()
        await self._release()

    async def close(self) -> None:
        for _dev, task in self._devs.values():
            await _cancel(task)
        if self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)
        await self._release()  # key-ups still reach uinput before we let go
        for dev, _task in self._devs.values():
            with contextlib.suppress(OSError):
                dev.ungrab()
            dev.close()
        self._devs.clear()

    async def vendor_report(self, report: bytes) -> None:
        res = identify_vendor(report)
        if res is None:
            return
        button, pressed = res
        if pressed and button is not None:
            if button not in self._top_down:
                self._top_down.append(button)
            await self._mapper.handle(button, 1)
        elif not pressed:
            while self._top_down:
                await self._mapper.handle(self._top_down.pop(), 0)

    async def _release(self) -> None:
        self._top_down.clear()
        try:
            await self._mapper.release_all()
        except Exception:
            logger.exception("release_all fehlgeschlagen")

    async def _read(self, source: str, dev: Any) -> None:
        pending = False
        try:
            async for event in dev.async_read_loop():
                try:
                    pending = await self._dispatch(source, event, pending)
                except Exception:
                    logger.exception("Fehler bei Ereignis von %s", dev.path)
        except OSError as e:
            if e.errno == errno.ENODEV:
                logger.info("%s entfernt", dev.path)
            else:
                logger.exception("Lesefehler auf %s", dev.path)
        except Exception:
            logger.exception("Lesefehler auf %s", dev.path)
        # Read loop ended: the device is gone or unusable.
        if self._devs.get(dev.path, (None,))[0] is dev:
            del self._devs[dev.path]
            dev.close()
            await self._release()

    async def _dispatch(self, source: str, event: Any, pending: bool) -> bool:
        """Handle one event; returns whether forwarded output awaits a sync."""
        t, code, value = event.type, event.code, event.value
        if t == E.EV_SYN:
            if code == E.SYN_REPORT and pending:
                self._out.sync()
                return False
            return pending
        if t == E.EV_MSC:
            return pending
        button = identify(source, t, code, value)
        if button is not None:
            await self._mapper.handle(button, value)
            return pending
        if t == E.EV_REL and code not in _WHEEL_AXES:
            self._out.rel(code, value)
            return True
        if t == E.EV_KEY:
            self._out.key(code, value)
            return True
        return pending


async def _cancel(task: asyncio.Task) -> None:
    if task is asyncio.current_task():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


class HidrawReader:
    """Reads vendor input reports (if01 hidraw) and hands them to on_report."""

    def __init__(self, path: str, on_report: Callable[[bytes], Awaitable[None]], *,
                 opener: Callable[[str, int], int] = os.open) -> None:
        self.path = path
        self._on_report = on_report
        self._opener = opener
        self._fd = -1
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        self._fd = self._opener(self.path, os.O_RDONLY | os.O_NONBLOCK)
        loop = asyncio.get_running_loop()
        loop.add_reader(self._fd, self._readable)
        self._task = loop.create_task(self._run())

    def _readable(self) -> None:
        try:
            data = os.read(self._fd, 64)
        except BlockingIOError:
            return
        except OSError as e:
            logger.info("hidraw %s beendet: %s", self.path, e)
            data = b""
        if not data:
            self._stop_reading()
            self._queue.put_nowait(None)
        else:
            self._queue.put_nowait(data)

    async def _run(self) -> None:
        while (data := await self._queue.get()) is not None:
            try:
                await self._on_report(data)
            except Exception:
                logger.exception("Fehler bei hidraw-Report von %s", self.path)

    def _stop_reading(self) -> None:
        if self._fd >= 0:
            asyncio.get_running_loop().remove_reader(self._fd)
            os.close(self._fd)
            self._fd = -1

    async def close(self) -> None:
        self._stop_reading()
        if self._task is not None:
            await _cancel(self._task)
