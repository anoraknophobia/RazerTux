"""System tray icon: profile switch, battery, window toggle, restore."""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QByteArray, QCoreApplication, QObject, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon, QWidget

from naga_config.gui.dbus_client import DaemonClient, DaemonError
from naga_config.gui.labels import battery_text
from naga_config.gui.mouse_view import themed_svg

# German names for the field names restore_backup() reports as failed
_FIELD_NAMES = {
    "poll_rate": QT_TRANSLATE_NOOP("TrayIcon", "Abfragerate"),
    "dpi_stages": QT_TRANSLATE_NOOP("TrayIcon", "DPI-Stufen"),
    "dpi": QT_TRANSLATE_NOOP("TrayIcon", "DPI"),
    "brightness.logo": QT_TRANSLATE_NOOP("TrayIcon", "Helligkeit Logo"),
    "brightness.scroll": QT_TRANSLATE_NOOP("TrayIcon", "Helligkeit Mausrad"),
    "brightness.thumb": QT_TRANSLATE_NOOP("TrayIcon", "Helligkeit Daumenfeld"),
    "idle_time": QT_TRANSLATE_NOOP("TrayIcon", "Leerlaufzeit"),
    "low_battery_threshold": QT_TRANSLATE_NOOP("TrayIcon", "Akku-Energiesparschwelle"),
}


def _tr(text: str) -> str:
    return QCoreApplication.translate("TrayIcon", text)


def app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(QByteArray(themed_svg("naga_v2_pro_top.svg", QGuiApplication.palette())))
    p = QPainter(pm)
    r.render(p, pm.rect().adjusted(14, 0, -14, 0).toRectF())
    p.end()
    return QIcon(pm)


class TrayIcon(QSystemTrayIcon):
    toggleWindowRequested = Signal()
    showWindowRequested = Signal()
    quitRequested = Signal()
    themeRequested = Signal(str)  # "synapse" | "system"

    def __init__(self, client: DaemonClient, parent: QObject | None = None,
                 dialog_parent: QWidget | None = None):
        super().__init__(app_icon(), parent)
        self.client = client
        self._dialog_parent = dialog_parent
        self._theme = "synapse"
        self.setToolTip(self.tr("Naga-Konfiguration"))
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

    def set_theme_checked(self, name: str) -> None:
        self._theme = name
        self.rebuild_menu()

    # -- menu -----------------------------------------------------------------
    def rebuild_menu(self) -> None:
        m = self.menu
        m.clear()
        up = self.client.is_available
        if not up:
            m.addAction(self.tr("Dienst läuft nicht")).setEnabled(False)
        else:
            active = self.client.active_profile()
            group = QActionGroup(m)
            group.setExclusive(True)
            profiles = self.client.list_profiles()
            names = dict(profiles)
            m.addAction(self.tr("Aktives Profil: %1").replace("%1", names.get(active, active))).setEnabled(False)
            for pid, name in profiles:
                a = m.addAction(name)
                a.setCheckable(True)
                a.setChecked(pid == active)
                group.addAction(a)
                a.triggered.connect(lambda _=False, pid=pid: self.client.activate_profile(pid))
            m.addSeparator()
            m.addAction(battery_text(self.client.status())).setEnabled(False)
        m.addSeparator()
        colours = m.addAction(self.tr("Systemfarben verwenden"))
        colours.setCheckable(True)
        colours.setChecked(self._theme == "system")
        colours.triggered.connect(lambda on: self.themeRequested.emit("system" if on else "synapse"))
        m.addAction(self.tr("Fenster öffnen")).triggered.connect(lambda _=False: self.showWindowRequested.emit())
        m.addAction(self.tr("Originalzustand wiederherstellen")).triggered.connect(lambda _=False: self.restore_original())
        m.addSeparator()
        m.addAction(self.tr("Beenden")).triggered.connect(lambda _=False: self.quitRequested.emit())

    def menu_texts(self) -> list[str]:
        return [a.text() for a in self.menu.actions() if not a.isSeparator()]

    def restore_original(self) -> None:
        if not self._confirm(self.tr("Originalzustand wiederherstellen"),
                             self.tr("Die gesicherten Originalwerte werden auf die Maus zurückgeschrieben: "
                                     "DPI, DPI-Stufen, Abfragerate, Helligkeit und Energie-Einstellungen "
                                     "(Leerlaufzeit, Energiesparschwelle). Effekte und Farben gehören nicht "
                                     "dazu – die kommen durch Aus- und wieder Einstecken der Maus zurück. "
                                     "Fortfahren?")):
            return
        try:
            failed = self.client.restore_original()
        except DaemonError as e:
            self._info(self.tr("Wiederherstellen fehlgeschlagen"), str(e))
            return
        if failed is None:
            self._info(self.tr("Wiederherstellen nicht möglich"), self.tr("Der Dienst läuft nicht."))
        elif failed:
            names = ", ".join(_tr(_FIELD_NAMES[f]) if f in _FIELD_NAMES else f for f in failed)
            self._info(self.tr("Teilweise wiederhergestellt"),
                       self.tr("Folgende Einstellungen konnten nicht wiederhergestellt werden:") + f"\n{names}")
        else:
            self._info(self.tr("Wiederhergestellt"), self.tr("Die Originaleinstellungen wurden zurückgeschrieben."))
