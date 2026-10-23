"""D-Bus interface io.github.razertux.Daemon1 (session bus, dbus-next): device-aware,
every call names the device model.

No `from __future__ import annotations` here: dbus-next reads the D-Bus
signatures from the (string) annotations.
"""
import asyncio
import logging
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from dbus_next import DBusError, Message, MessageFlag, MessageType, Variant
from dbus_next.service import ServiceInterface
from dbus_next.service import method as _dbus_method
from dbus_next.service import signal as _dbus_signal

from razertux.backup import load_backup
from razertux.config.store import ConfigStore
from razertux.hw.button_maps import InputSignal
from razertux.i18n import apply_settings as apply_language, tr

from .session import DeviceSession, NotConnected, ReadOnly

logger = logging.getLogger(__name__)

BUS_NAME = "io.github.razertux"
OBJECT_PATH = "/io/github/razertux"
INTERFACE = "io.github.razertux.Daemon1"
VERSION = "1"
_ERR = "io.github.razertux.Error."
PAUSE_TIMEOUT = 300.0  # seconds a recorder may keep a device paused
RETRY_DELAY = 5.0  # seconds before a failed auto-resume is tried again


def openrazer_loaded() -> bool:
    return Path("/sys/module/razermouse").exists()


def _quiet(decorator_factory):
    """dbus-next's @method/@signal call typing.no_type_check_decorator,
    deprecated since Python 3.13 (removed in 3.15); silence just that."""
    def factory(*args, **kwargs):
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*no_type_check_decorator",
                                    category=DeprecationWarning)
            return decorator_factory(*args, **kwargs)
    return factory


method = _quiet(_dbus_method)
signal = _quiet(_dbus_signal)


class Daemon1Service(ServiceInterface):
    """D-Bus methods over the daemon's DeviceSessions (one per model). The profile of
    a model lives in its session and in Settings.active_profiles; plain methods
    (activate, reload, ...) hold the logic, the D-Bus methods are thin wrappers."""

    def __init__(self, store: ConfigStore, sessions: Callable[[], Mapping[str, DeviceSession]], *,
                 openrazer_loaded: Callable[[], bool] = openrazer_loaded) -> None:
        super().__init__(INTERFACE)
        self._store = store
        self._sessions = sessions
        self._openrazer = openrazer_loaded
        self._seen: dict[str, tuple[bool, str]] = {}  # model -> (connected, connection)
        self.pause_timeout = PAUSE_TIMEOUT
        # model -> (callers' unique bus names, auto-resume timer)
        self._pauses: dict[str, tuple[set[str], asyncio.TimerHandle]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._bus: Any = None
        apply_language(store.load_settings())  # language of config errors and notifications

    # -- logic ---------------------------------------------------------------

    def session(self, model: str) -> DeviceSession:
        try:
            return self._sessions()[model]
        except KeyError:
            raise DBusError(_ERR + "UnknownDevice",
                            tr("daemon", "Unknown device: {model}", model=model)) from None

    def list_devices(self) -> list[list]:
        out = []
        for model, session in self._sessions().items():
            st = session.status()
            out.append([model, session.info.name, str(st["connection"]), bool(st["connected"])])
        return out

    def list_profiles(self, model: str) -> list[list[str]]:
        return [[p.id, p.name] for p in self.session(model).store.list_profiles()]

    async def activate(self, model: str, profile_id: str) -> None:
        session = self.session(model)
        try:
            await session.activate(profile_id)
        except KeyError:
            raise DBusError(_ERR + "UnknownProfile",
                            tr("daemon", "Unknown profile: {profile}", profile=profile_id)) from None
        self.ProfileChanged(model, session.active_profile_id)

    async def reload(self) -> None:
        """Re-read settings, macros and every model's active profile from disk."""
        self._store.errors.clear()
        apply_language(self._store.load_settings())
        self._store.errors.clear()  # settings.json errors again, in the (new) language
        sessions = dict(self._sessions())
        old = {m: s.profile.id if s.profile else None for m, s in sessions.items()}
        for session in sessions.values():
            try:
                await session.reload()
            except Exception:
                logger.exception("%s: reload failed", session.info.name)
        for model, session in sessions.items():
            if session.active_profile_id != old[model]:
                self.ProfileChanged(model, session.active_profile_id)

    def _spawn(self, coro: Any) -> asyncio.Task:
        """Run coro as a kept task; failures are logged."""
        def done(task: asyncio.Task) -> None:
            self._tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                logger.error("Background task failed", exc_info=task.exception())

        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(done)
        return task

    async def attach(self, bus: Any) -> None:
        """Watch the bus so a recorder that dies cannot leave a device unmapped.

        dbus-next hands a ServiceInterface method no sender. PauseDevice and
        ResumeDevice calls to our path are therefore answered here, in a message
        handler (which sees msg.sender and runs before dispatch); the decorated
        methods below stay for introspection and direct use. NameOwnerChanged
        resumes the models a vanished caller paused."""
        self._bus = bus

        def on_message(msg: Message) -> bool:
            if (msg.message_type == MessageType.METHOD_CALL and msg.path == OBJECT_PATH
                    and msg.interface == INTERFACE and msg.signature == "s"
                    and msg.member in ("PauseDevice", "ResumeDevice")):
                self._spawn(self._serve(msg))
                return True
            if (msg.message_type == MessageType.SIGNAL and msg.member == "NameOwnerChanged"
                    and msg.sender == "org.freedesktop.DBus" and msg.signature == "sss"):
                self._spawn(self.name_owner_changed(*msg.body))
            return False

        bus.add_message_handler(on_message)
        await bus.call(Message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="AddMatch", signature="s",
            body=["type='signal',sender='org.freedesktop.DBus',"
                  "interface='org.freedesktop.DBus',member='NameOwnerChanged'"]))

    async def _serve(self, msg: Message) -> None:
        model = msg.body[0]
        try:
            if msg.member == "PauseDevice":
                await self.pause_device(model, msg.sender)
            else:
                await self.resume_device(model)
            reply = Message.new_method_return(msg)
        except DBusError as e:
            reply = Message.new_error(msg, e.type, e.text)
        except Exception as e:
            logger.exception("%s failed", msg.member)
            reply = Message.new_error(msg, _ERR + "Failed", str(e))
        if not msg.flags & MessageFlag.NO_REPLY_EXPECTED:
            self._bus.send(reply)

    async def name_owner_changed(self, name: str, old_owner: str, new_owner: str) -> None:
        if new_owner or not name:
            return
        for model, (senders, _timer) in list(self._pauses.items()):
            if name in senders:
                senders.discard(name)
                if not senders:
                    try:
                        await self.resume_device(model)
                    except Exception:
                        logger.exception("%s: resume after caller left failed - "
                                         "the timeout retries", model)

    async def pause_device(self, model: str, sender: str | None = None) -> None:
        """Release the model's grabs and stop mapping until resume_device, every caller
        has left the bus, or pause_timeout seconds pass (the mouse must never stay
        unmapped). Idempotent; a second pause keeps the first deadline. A pause without
        a known sender (direct use) is only ended by resume or the timeout."""
        session = self.session(model)
        entry = self._pauses.get(model)
        if entry is None:
            entry = self._pauses[model] = (set(), self._arm(model, self.pause_timeout))
        if sender:
            entry[0].add(sender)
        await session.pause()

    def _arm(self, model: str, delay: float) -> asyncio.TimerHandle:
        return asyncio.get_running_loop().call_later(
            delay, lambda: self._spawn(self._timed_out(model)))

    async def _timed_out(self, model: str) -> None:
        logger.warning("%s: pause timed out - mapping resumes", model)
        try:
            await self.resume_device(model)
        except Exception:
            logger.exception("%s: resume after timeout failed - retrying", model)
            entry = self._pauses.get(model)
            if entry is not None:
                self._pauses[model] = (entry[0], self._arm(model, RETRY_DELAY))

    async def resume_device(self, model: str) -> None:
        session = self.session(model)
        await session.resume()  # on failure the pause state (and its timer) stays
        entry = self._pauses.pop(model, None)
        if entry is not None:
            entry[1].cancel()

    def shutdown(self) -> None:
        """Daemon stops: forget pauses (the sessions' close releases everything)."""
        for _senders, timer in self._pauses.values():
            timer.cancel()
        self._pauses.clear()

    def status(self, model: str) -> dict[str, Variant]:
        session = self.session(model)
        st = session.status()
        return {
            "connected": Variant("b", bool(st["connected"])),
            "connection": Variant("s", st["connection"]),
            "battery": Variant("i", int(st["battery"])),
            "charging": Variant("b", bool(st["charging"])),
            "scroll_stage": Variant("i", int(st.get("scroll_stage", -1))),
            "dpi": Variant("i", int(st.get("dpi", -1))),
            "openrazer_loaded": Variant("b", bool(self._openrazer())),
            "config_errors": Variant("as", session.config_errors()),
            "writable": Variant("b", bool(st["writable"])),
            "paused": Variant("b", bool(st.get("paused", False))),
            "experimental": Variant("b", not st["verified"]),
            "model": Variant("s", model),
            "name": Variant("s", session.info.name),
        }

    def notify_status(self, model: str) -> None:
        session = self.session(model)
        st = session.status()
        now = (bool(st["connected"]), str(st["connection"]))
        if self._seen.get(model) != now:
            self._seen[model] = now
            self.DevicesChanged()
        self.StatusChanged(model, self.status(model))

    def devices_changed(self) -> None:
        """A session appeared (first sight of a new model)."""
        self.DevicesChanged()

    def button_learned(self, model: str, signal: InputSignal) -> None:
        self.ButtonLearned(model, signal.source, int(signal.ev_type), int(signal.code))

    def restore_original(self, model: str) -> list[str]:
        """Write the backup back to the mouse; returns the fields that could not be restored."""
        session = self.session(model)
        try:
            backup = load_backup(session.backup_path)
        except (OSError, ValueError) as e:
            raise DBusError(_ERR + "NoBackup",
                            tr("daemon", "No readable backup at {path}: {error}",
                               path=session.backup_path, error=e)) from e
        try:
            return list(session.restore_backup(backup) or [])
        except NotConnected:
            raise DBusError(_ERR + "NotConnected", tr("daemon", "Mouse not connected")) from None
        except ReadOnly:
            raise DBusError(_ERR + "ReadOnly", tr("daemon", "Mouse is read-only")) from None

    # -- D-Bus ---------------------------------------------------------------

    @method()
    def GetVersion(self) -> 's':
        return VERSION

    @method()
    def ListDevices(self) -> 'a(sssb)':
        return self.list_devices()

    @method()
    def ListProfiles(self, model: 's') -> 'a(ss)':
        return self.list_profiles(model)

    @method()
    def GetActiveProfile(self, model: 's') -> 's':
        return self.session(model).active_profile_id

    @method()
    async def ActivateProfile(self, model: 's', profile_id: 's'):
        await self.activate(model, profile_id)

    @method()
    async def Reload(self):
        await self.reload()

    @method()
    def GetStatus(self, model: 's') -> 'a{sv}':
        return self.status(model)

    @method()
    def RestoreOriginal(self, model: 's') -> 'as':
        return self.restore_original(model)

    @method()
    def GetDiagnostics(self, model: 's') -> 's':
        return self.session(model).diagnostics()

    @method()
    async def StartLearning(self, model: 's'):
        await self.session(model).start_learning()

    @method()
    async def StopLearning(self, model: 's'):
        await self.session(model).stop_learning()

    @method()
    async def PauseDevice(self, model: 's'):
        await self.pause_device(model)  # real calls are served by attach()'s handler

    @method()
    async def ResumeDevice(self, model: 's'):
        await self.resume_device(model)

    @signal()
    def DevicesChanged(self):
        pass

    @signal()
    def ProfileChanged(self, model, profile_id) -> 'ss':
        return [model, profile_id]

    @signal()
    def StatusChanged(self, model, status) -> 'sa{sv}':
        return [model, status]

    @signal()
    def ButtonLearned(self, model, source, type, code) -> 'ssii':
        return [model, source, type, code]
