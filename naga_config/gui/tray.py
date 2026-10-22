"""System tray icon: profile switch, battery, window toggle, restore."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QObject, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon, QWidget

from naga_config.gui.dbus_client import DaemonClient, DaemonError
from naga_config.gui.mouse_view import themed_svg

_FIELD_NAMES = {"dpi": "DPI-Stufen", "poll_rate": "Abtastrate", "lighting": "Beleuchtung",
                "scroll_profile": "Mausrad-Profil"}


def app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(QByteArray(themed_svg("naga_v2_pro_top.svg", QGuiApplication.palette())))
    p = QPainter(pm)
    r.render(p, pm.rect().adjusted(14, 0, -14, 0).toRectF())
    p.end()
    return QIcon(pm)


def battery_text(status: dict) -> str:
    b = int(status.get("battery", -1)) if status else -1
    if b < 0:
        return "Akku: unbekannt"
    return f"Akku: {b} %" + (" (lädt)" if status.get("charging") else "")


class TrayIcon(QSystemTrayIcon):
    toggleWindowRequested = Signal()
    showWindowRequested = Signal()
    quitRequested = Signal()

    def __init__(self, client: DaemonClient, parent: QObject | None = None,
                 dialog_parent: QWidget | None = None):
        super().__init__(app_icon(), parent)
        self.client = client
        self._dialog_parent = dialog_parent
        self.setToolTip("Naga-Konfiguration")
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.rebuild_menu)
        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)
        client.profileChanged.connect(lambda _id: self.rebuild_menu())
        client.statusChanged.connect(lambda _s: self.rebuild_menu())
        client.available.connect(lambda _u: self.rebuild_menu())
        self.rebuild_menu()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # left click
            self.toggleWindowRequested.emit()

    # -- hooks (replaced in tests) --------------------------------------------
    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self._dialog_parent, title, text) == QMessageBox.StandardButton.Yes

    def _info(self, title: str, text: str) -> None:
        QMessageBox.information(self._dialog_parent, title, text)

    # -- menu -----------------------------------------------------------------
    def rebuild_menu(self) -> None:
        m = self.menu
        m.clear()
        up = self.client.is_available
        if not up:
            m.addAction("Dienst läuft nicht").setEnabled(False)
        else:
            active = self.client.active_profile()
            group = QActionGroup(m)
            group.setExclusive(True)
            profiles = self.client.list_profiles()
            names = dict(profiles)
            m.addAction(f"Aktives Profil: {names.get(active, active)}").setEnabled(False)
            for pid, name in profiles:
                a = m.addAction(name)
                a.setCheckable(True)
                a.setChecked(pid == active)
                group.addAction(a)
                a.triggered.connect(lambda _=False, pid=pid: self.client.activate_profile(pid))
            m.addSeparator()
            m.addAction(battery_text(self.client.status())).setEnabled(False)
        m.addSeparator()
        m.addAction("Fenster öffnen").triggered.connect(lambda _=False: self.showWindowRequested.emit())
        m.addAction("Originalzustand wiederherstellen").triggered.connect(lambda _=False: self.restore_original())
        m.addSeparator()
        m.addAction("Beenden").triggered.connect(lambda _=False: self.quitRequested.emit())

    def menu_texts(self) -> list[str]:
        return [a.text() for a in self.menu.actions() if not a.isSeparator()]

    def restore_original(self) -> None:
        if not self._confirm("Originalzustand wiederherstellen",
                             "Die gesicherten Originaleinstellungen werden auf die Maus zurückgeschrieben "
                             "(DPI, Abtastrate, Beleuchtung). Fortfahren?"):
            return
        try:
            failed = self.client.restore_original()
        except DaemonError as e:
            self._info("Wiederherstellen fehlgeschlagen", str(e))
            return
        if failed is None:
            self._info("Wiederherstellen nicht möglich", "Der Dienst läuft nicht.")
        elif failed:
            names = ", ".join(_FIELD_NAMES.get(f, f) for f in failed)
            self._info("Teilweise wiederhergestellt",
                       f"Folgende Einstellungen konnten nicht wiederhergestellt werden:\n{names}")
        else:
            self._info("Wiederhergestellt", "Die Originaleinstellungen wurden zurückgeschrieben.")
