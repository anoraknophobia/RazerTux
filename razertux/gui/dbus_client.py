"""Client for the daemon's D-Bus API, exposed as a QObject with Qt signals.

Transport note: PySide6 (6.12) QtDBus cannot demarshal the daemon's container
replies (a(ss), a{sv}) - QDBusArgument reading is unusable from Python. The
real transport therefore uses dbus-next on a private asyncio thread; results
and signals reach the GUI thread through Qt signals, so the Qt event loop is
never blocked by or entangled with asyncio. DaemonClient only needs the tiny
Transport interface (call/start/callbacks), which tests replace with a fake.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QObject, Signal

from razertux.config.model import NAGA_MODEL

BUS_NAME = "io.github.razertux"
OBJECT_PATH = "/io/github/razertux"
INTERFACE = "io.github.razertux.Daemon1"
CALL_TIMEOUT = 5.0
# the service on the bus has no Daemon1 interface: it is an older RazerTux
VERSION_MISMATCH = QT_TRANSLATE_NOOP("DaemonClient", "Service version does not match – please restart the RazerTux service (systemctl --user restart razertuxd) or log in again")
NOT_RUNNING = QT_TRANSLATE_NOOP("DaemonClient", "Service is not running")
_OLD_DAEMON_ERRORS = ("org.freedesktop.DBus.Error.UnknownMethod",
                      "org.freedesktop.DBus.Error.UnknownInterface",
                      "org.freedesktop.DBus.Error.UnknownObject")


class DaemonUnavailable(Exception):
    """The daemon is not on the bus."""


class DaemonError(Exception):
    """The daemon answered with a D-Bus error."""

    def __init__(self, name: str, text: str):
        super().__init__(text or name)
        self.name = name


def _plain(v: Any) -> Any:
    """Strip dbus-next Variants."""
    if hasattr(v, "signature") and hasattr(v, "value"):
        return _plain(v.value)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return type(v)(_plain(x) for x in v)
    return v


class DBusNextTransport(QObject):
    """Transport over dbus-next on a background thread."""

    signalReceived = Signal(str, object)
    ownerChanged = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._loop = asyncio.new_event_loop()
        self._bus: Any = None
        self._lock: asyncio.Lock | None = None
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True,
                                        name="naga-dbus")

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()
            asyncio.run_coroutine_threadsafe(self._watch(), self._loop)

    def stop(self) -> None:
        async def _close() -> None:
            if self._bus is not None:
                self._bus.disconnect()
            self._loop.stop()

        if self._thread.is_alive():
            asyncio.run_coroutine_threadsafe(_close(), self._loop)
            self._thread.join(2)
        if not self._loop.is_running():
            self._loop.close()

    async def _connect(self) -> Any:
        from dbus_next import BusType
        from dbus_next.aio import MessageBus
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:  # no duplicate concurrent connects
            if self._bus is None or not self._bus.connected:
                self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
                self._bus.add_message_handler(self._on_message)
        return self._bus

    def _on_message(self, msg: Any) -> None:
        from dbus_next import MessageType
        if msg.message_type != MessageType.SIGNAL:
            return
        if msg.interface == INTERFACE:
            self.signalReceived.emit(msg.member, _plain(list(msg.body)))
        elif msg.member == "NameOwnerChanged" and msg.body and msg.body[0] == BUS_NAME:
            self.ownerChanged.emit(bool(msg.body[2]))

    async def _watch(self) -> None:
        from dbus_next import Message
        try:
            bus = await self._connect()
            for rule in (f"type='signal',interface='{INTERFACE}'",
                         f"type='signal',sender='org.freedesktop.DBus',member='NameOwnerChanged',arg0='{BUS_NAME}'"):
                await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                       interface="org.freedesktop.DBus", member="AddMatch",
                                       signature="s", body=[rule]))
        except Exception:  # no session bus: stays "unavailable"
            pass

    async def _call(self, member: str, signature: str, args: tuple) -> list:
        from dbus_next import DBusError, Message, MessageType
        try:
            bus = await self._connect()
            reply = await asyncio.wait_for(bus.call(Message(
                destination=BUS_NAME, path=OBJECT_PATH, interface=INTERFACE,
                member=member, signature=signature, body=list(args))), CALL_TIMEOUT)
        except DBusError as e:
            raise DaemonUnavailable(str(e)) from e
        except Exception as e:  # no/invalid bus address, timeout, closed connection ...
            raise DaemonUnavailable(f"{type(e).__name__}: {e}") from e
        if reply.message_type == MessageType.ERROR:
            if reply.error_name in ("org.freedesktop.DBus.Error.ServiceUnknown",
                                    "org.freedesktop.DBus.Error.NameHasNoOwner"):
                raise DaemonUnavailable(reply.error_name)
            if reply.error_name in _OLD_DAEMON_ERRORS:  # only the pre-Daemon1 interface
                raise DaemonUnavailable(VERSION_MISMATCH)
            raise DaemonError(reply.error_name, str(reply.body[0]) if reply.body else "")
        return _plain(list(reply.body))

    def call(self, member: str, signature: str = "", args: tuple = ()) -> list:
        fut = asyncio.run_coroutine_threadsafe(self._call(member, signature, tuple(args)), self._loop)
        try:
            return fut.result(CALL_TIMEOUT + 1)
        except TimeoutError as e:
            raise DaemonUnavailable("Timeout") from e


class DaemonClient(QObject):
    """Qt face of the Daemon1 API. Every call takes the device `model` (default: the
    Naga V2 Pro, the model the main window works on until it has a device selector);
    signals carry the model."""

    profileChanged = Signal(str, str)  # model, profile id
    statusChanged = Signal(str, dict)  # model, status
    devicesChanged = Signal()
    buttonLearned = Signal(str, str, int, int)  # model, source, type, code
    available = Signal(bool)
    reloading = Signal()  # a Reload was requested (the daemon re-applies profiles)

    def __init__(self, transport: Any | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._t = transport if transport is not None else DBusNextTransport()
        self._available: bool | None = None
        self._mismatch = False
        if hasattr(self._t, "signalReceived"):
            self._t.signalReceived.connect(self.handle_signal)
            self._t.ownerChanged.connect(self._owner_changed)
        self._t.start()

    @property
    def is_available(self) -> bool:
        return bool(self._available)

    @property
    def version_mismatch(self) -> bool:
        """The service on the bus is too old (no Daemon1 interface)."""
        return self._mismatch

    def unavailable_text(self) -> str:
        """Why there is no daemon, ready to show (translated)."""
        return QCoreApplication.translate("DaemonClient", VERSION_MISMATCH if self._mismatch
                                          else NOT_RUNNING)

    def _set_available(self, up: bool) -> None:
        if self._available is not up:
            self._available = up
            self.available.emit(up)

    def _owner_changed(self, up: bool) -> None:
        if up:
            self.refresh()
        else:
            self._set_available(False)

    def refresh(self) -> None:
        """Re-read state (e.g. after the daemon appeared) and publish it."""
        self.devicesChanged.emit()
        for model, *_ in self.devices():
            status = self.status(model)
            if status:
                self.statusChanged.emit(model, status)
                active = self.active_profile(model)
                if active:
                    self.profileChanged.emit(model, active)

    def _call(self, member: str, signature: str = "", args: tuple = ()) -> list | None:
        """Result list, or None when the daemon is unreachable."""
        try:
            res = self._t.call(member, signature, args)
        except DaemonUnavailable as e:
            self._mismatch = str(e) == VERSION_MISMATCH
            self._set_available(False)
            return None
        except DaemonError:
            self._mismatch = False
            self._set_available(True)
            raise
        self._mismatch = False
        self._set_available(True)
        return res

    def handle_signal(self, member: str, body: list) -> None:
        if member == "ProfileChanged" and len(body) >= 2:
            self.profileChanged.emit(str(body[0]), str(body[1]))
        elif member == "StatusChanged" and len(body) >= 2:
            self.statusChanged.emit(str(body[0]), dict(body[1]))
        elif member == "DevicesChanged":
            self.devicesChanged.emit()
        elif member == "ButtonLearned" and len(body) >= 4:
            self.buttonLearned.emit(str(body[0]), str(body[1]), int(body[2]), int(body[3]))

    # -- mirrors of the D-Bus methods; defaults when the daemon is away -------
    def version(self) -> str:
        res = self._call("GetVersion")
        return str(res[0]) if res else ""

    def devices(self) -> list[tuple[str, str, str, bool]]:
        """(model, name, connection, connected) of every device the daemon knows."""
        res = self._call("ListDevices")
        return [(str(m), str(n), str(c), bool(up)) for m, n, c, up in res[0]] if res else []

    def list_profiles(self, model: str = NAGA_MODEL) -> list[tuple[str, str]]:
        res = self._call("ListProfiles", "s", (model,))
        return [tuple(p) for p in res[0]] if res else []

    def active_profile(self, model: str = NAGA_MODEL) -> str:
        res = self._call("GetActiveProfile", "s", (model,))
        return str(res[0]) if res else ""

    def activate_profile(self, profile_id: str, model: str = NAGA_MODEL) -> bool:
        return self._call("ActivateProfile", "ss", (model, profile_id)) is not None

    def reload(self) -> bool:
        self.reloading.emit()
        return self._call("Reload") is not None

    def status(self, model: str = NAGA_MODEL) -> dict:
        res = self._call("GetStatus", "s", (model,))
        return dict(res[0]) if res else {}

    def restore_original(self, model: str = NAGA_MODEL) -> list[str] | None:
        """Fields that could not be restored, or None if the daemon is away."""
        res = self._call("RestoreOriginal", "s", (model,))
        return list(res[0]) if res else None

    def diagnostics(self, model: str = NAGA_MODEL) -> str:
        res = self._call("GetDiagnostics", "s", (model,))
        return str(res[0]) if res else ""

    def set_scroll_stage(self, stage: int, model: str = NAGA_MODEL) -> bool:
        """Switch the mouse to scroll wheel stage 1-6 (the Naga's front top button stages)."""
        return self._call("SetScrollStage", "si", (model, int(stage))) is not None

    def set_custom_scroll(self, tension: int, steps: int, model: str = NAGA_MODEL) -> bool:
        """Write the Naga's "Custom" scroll wheel stage (experimental)."""
        return self._call("SetCustomScroll", "sii", (model, int(tension), int(steps))) is not None

    def start_learning(self, model: str = NAGA_MODEL) -> bool:
        return self._call("StartLearning", "s", (model,)) is not None

    def stop_learning(self, model: str = NAGA_MODEL) -> bool:
        return self._call("StopLearning", "s", (model,)) is not None

    def pause_device(self, model: str = NAGA_MODEL) -> bool:
        """Release the device's grabs so a recorder can read it raw. The daemon resumes
        by itself after 300 s or when this process leaves the bus."""
        return self._call("PauseDevice", "s", (model,)) is not None

    def resume_device(self, model: str = NAGA_MODEL) -> bool:
        return self._call("ResumeDevice", "s", (model,)) is not None
