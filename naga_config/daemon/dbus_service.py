"""D-Bus interface io.github.nagaconfig.Daemon (session bus, dbus-next).

No `from __future__ import annotations` here: dbus-next reads the D-Bus
signatures from the (string) annotations.
"""
import logging
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from dbus_next import DBusError, Variant
from dbus_next.service import ServiceInterface
from dbus_next.service import method as _dbus_method
from dbus_next.service import signal as _dbus_signal

from naga_config.backup import load_backup
from naga_config.config.model import Profile, validate
from naga_config.config.store import ConfigStore

logger = logging.getLogger(__name__)

BUS_NAME = "io.github.nagaconfig"
OBJECT_PATH = "/io/github/nagaconfig"
INTERFACE = "io.github.nagaconfig.Daemon"
_ERR = "io.github.nagaconfig.Error."


class Hardware(Protocol):
    def hw_status(self) -> dict[str, Any]:
        """connected (bool), connection (str), battery (int, -1 unknown), charging (bool)."""

    def scroll_profile_supported(self) -> bool: ...


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


class NagaService(ServiceInterface):
    """Profile state + D-Bus methods. Plain async methods (activate, reload, ...)
    hold the logic; the D-Bus methods are thin wrappers."""

    def __init__(self, store: ConfigStore, mapper: Any, hw: Hardware, *,
                 apply_device_settings: Callable[[Profile], None] = lambda p: None,
                 restore: Callable[[dict], list[str] | None] | None = None,
                 openrazer_loaded: Callable[[], bool] = openrazer_loaded,
                 backup_path: Path | None = None) -> None:
        super().__init__(INTERFACE)
        self._store = store
        self._mapper = mapper
        self._hw = hw
        self._apply = apply_device_settings
        self._restore = restore
        self._openrazer = openrazer_loaded
        self._backup_path = backup_path or (
            Path.home() / ".config" / "naga-config" / "mouse-backup.json")
        self._settings = store.load_settings()
        self.active_profile_id = self._settings.active_profile
        self.active_profile: Profile | None = None
        self._macros: dict = {}
        self._validation: list[str] = []

    # -- logic ---------------------------------------------------------------

    def list_profiles(self) -> list[list[str]]:
        return [[p.id, p.name] for p in self._store.list_profiles()]

    async def activate(self, profile_id: str) -> None:
        known = {p.id for p in self._store.list_profiles()} | {"default"}
        if profile_id not in known:
            raise DBusError(_ERR + "UnknownProfile", f"Unbekanntes Profil: {profile_id}")
        profile = self._store.load_profile(profile_id)
        await self._use(profile)
        self._settings.active_profile = profile.id
        self._store.save_settings(self._settings)
        self.ProfileChanged(profile.id)
        self.notify_status()

    async def reload(self) -> None:
        """Re-read settings, macros and the active profile from disk."""
        self._store.errors.clear()
        old = self.active_profile_id
        self._settings = self._store.load_settings()
        self._macros = self._store.load_macros()
        self._mapper.set_macros(self._macros)
        profile = self._store.load_profile(self._settings.active_profile)
        await self._use(profile)
        if profile.id != old:
            self.ProfileChanged(profile.id)
        self.notify_status()

    async def _use(self, profile: Profile) -> None:
        await self._mapper.set_profile(profile)
        self.active_profile = profile
        self.active_profile_id = profile.id
        self._validation = validate(profile, self._macros)
        try:
            self._apply(profile)
        except Exception:
            logger.exception("Geräteeinstellungen für %s nicht angewendet", profile.id)

    def config_errors(self) -> list[str]:
        return list(dict.fromkeys([*self._store.errors, *self._validation]))

    def status(self) -> dict[str, Variant]:
        hw = self._hw.hw_status()
        return {
            "connected": Variant("b", bool(hw["connected"])),
            "connection": Variant("s", hw["connection"]),
            "battery": Variant("i", int(hw["battery"])),
            "charging": Variant("b", bool(hw["charging"])),
            "openrazer_loaded": Variant("b", bool(self._openrazer())),
            "config_errors": Variant("as", self.config_errors()),
        }

    def notify_status(self) -> None:
        self.StatusChanged(self.status())

    def restore_original(self) -> list[str]:
        """Write the backup back to the mouse; returns the fields that could not be restored."""
        try:
            backup = load_backup(self._backup_path)
        except (OSError, ValueError) as e:
            raise DBusError(_ERR + "NoBackup",
                            f"Keine lesbare Sicherung unter {self._backup_path}: {e}") from e
        if self._restore is None:
            raise DBusError(_ERR + "NotImplemented",
                            "Wiederherstellen des Originalzustands ist noch nicht umgesetzt")
        return list(self._restore(backup) or [])

    # -- D-Bus ---------------------------------------------------------------

    @method()
    def ListProfiles(self) -> 'a(ss)':
        return self.list_profiles()

    @method()
    def GetActiveProfile(self) -> 's':
        return self.active_profile_id

    @method()
    async def ActivateProfile(self, profile_id: 's'):
        await self.activate(profile_id)

    @method()
    async def Reload(self):
        await self.reload()

    @method()
    def GetStatus(self) -> 'a{sv}':
        return self.status()

    @method()
    def RestoreOriginal(self) -> 'as':
        return self.restore_original()

    @method()
    def ScrollProfileSupported(self) -> 'b':
        return bool(self._hw.scroll_profile_supported())

    @signal()
    def ProfileChanged(self, profile_id) -> 's':
        return profile_id

    @signal()
    def StatusChanged(self, status) -> 'a{sv}':
        return status
