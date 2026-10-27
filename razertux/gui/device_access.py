"""View → Device access: "RazerTux" (everything over hidraw) or "RazerTux + OpenRazer"
(features the OpenRazer daemon offers go through it). Saved in Settings.device_access;
the daemon re-routes on Reload."""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu

from razertux.config.model import Settings
from razertux.config.store import ConfigStore

CHOICES = (("razertux", "RazerTux"), ("openrazer", "RazerTux + OpenRazer"))


def _tr(text: str) -> str:
    return QCoreApplication.translate("DeviceAccess", text)


def banner_text(status: dict) -> str:
    """Hint for the main window's OpenRazer banner ("" = hide it)."""
    access = status.get("device_access", "razertux")
    if access == "openrazer-unavailable":
        return _tr("OpenRazer not running – RazerTux manages everything for now")
    if access == "razertux" and status.get("openrazer_loaded"):
        return _tr("OpenRazer detected – switch to “RazerTux + OpenRazer” under "
                   "View → Device access")
    return ""


class DeviceAccessMenu:
    def __init__(self, parent: QMenu, store: ConfigStore, settings: Settings, client):
        self.store, self.settings, self.client = store, settings, client
        self.menu = parent.addMenu(_tr("Device access"))
        self.menu.setToolTipsVisible(True)
        group = QActionGroup(self.menu)
        group.setExclusive(True)
        self.actions: dict[str, QAction] = {}
        for key, text in CHOICES:
            a = self.menu.addAction(text)
            a.setCheckable(True)
            a.setChecked(settings.device_access == key)
            group.addAction(a)
            a.triggered.connect(lambda _=False, k=key: self.choose(k))
            self.actions[key] = a
        self.set_available(False)

    def set_available(self, up: bool) -> None:
        a = self.actions["openrazer"]
        # never lock anyone into a mode they cannot leave
        a.setEnabled(up or self.settings.device_access == "openrazer")
        a.setToolTip("" if up else _tr("OpenRazer daemon not running"))

    def set_status(self, status: dict) -> None:
        if "openrazer_available" in status:
            self.set_available(bool(status["openrazer_available"]))

    def choose(self, mode: str) -> None:
        settings = self.store.load_settings()  # only this field; unsaved GUI edits stay
        settings.device_access = mode
        self.store.save_settings(settings)
        self.settings.device_access = mode
        self.actions[mode].setChecked(True)
        self.client.reload()
