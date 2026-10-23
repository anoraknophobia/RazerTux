"""System tray icon: profile switch, battery, window toggle, restore."""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QObject, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon, QWidget

from razertux.config.model import NAGA_MODEL
from razertux.gui.dbus_client import DaemonClient, DaemonError
from razertux.gui.labels import battery_text
from razertux.gui.language import add_language_menu
from razertux import APP_NAME
from razertux.gui.mouse_scene import ASSETS

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
    "scroll_mode": QT_TRANSLATE_NOOP("TrayIcon", "Mausrad-Modus"),
    "scroll_acceleration": QT_TRANSLATE_NOOP("TrayIcon", "Mausrad-Beschleunigung"),
    "scroll_smart_reel": QT_TRANSLATE_NOOP("TrayIcon", "Mausrad Smart Reel"),
}


def _tr(text: str) -> str:
    return QCoreApplication.translate("TrayIcon", text)


def app_icon() -> QIcon:
    """The RazerTux penguin (bundled SVG, the same file as the installed hicolor icon).
    Its white belly and green eyes keep it visible on dark and light panels alike."""
    return QIcon(str(ASSETS / "razertux.svg"))


class TrayIcon(QSystemTrayIcon):
    toggleWindowRequested = Signal()
    showWindowRequested = Signal()
    quitRequested = Signal()
    themeRequested = Signal(str)  # "synapse" | "system"
    languageRequested = Signal(str)  # Settings.language
    osdRequested = Signal(bool)  # Settings.osd

    def __init__(self, client: DaemonClient, parent: QObject | None = None,
                 dialog_parent: QWidget | None = None):
        super().__init__(app_icon(), parent)
        self.client = client
        self._dialog_parent = dialog_parent
        self._theme = "synapse"
        self._language = "system"
        self._osd = True
        self._language_menu: QMenu | None = None
        self.setToolTip(APP_NAME)
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.rebuild_menu)
        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)
        self._restore_model = NAGA_MODEL  # whose backup the flat menu entry restores
        client.profileChanged.connect(lambda *_: self.rebuild_menu())
        client.statusChanged.connect(lambda *_: self.rebuild_menu())
        client.devicesChanged.connect(self.rebuild_menu)
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

    def set_osd_checked(self, on: bool) -> None:
        self._osd = on
        self.rebuild_menu()

    def set_language_checked(self, name: str) -> None:
        self._language = name
        self.rebuild_menu()

    # -- menu -----------------------------------------------------------------
    def rebuild_menu(self) -> None:
        m = self.menu
        if self._language_menu is not None:  # clear() keeps submenus alive
            try:
                self._language_menu.deleteLater()
            except RuntimeError:  # already gone
                pass
            self._language_menu = None
        m.clear()
        flat = True
        try:
            flat = self._add_daemon_entries(m)
        except DaemonError:  # daemon going away mid-rebuild (e.g. restart)
            m.clear()
            m.addAction(self.tr("Dienst läuft nicht")).setEnabled(False)
        m.addSeparator()
        colours = m.addAction(self.tr("Systemfarben verwenden"))
        colours.setCheckable(True)
        colours.setChecked(self._theme == "system")
        colours.triggered.connect(lambda on: self.themeRequested.emit("system" if on else "synapse"))
        osd = m.addAction(self.tr("Bildschirmanzeige"))
        osd.setCheckable(True)
        osd.setChecked(self._osd)
        osd.triggered.connect(lambda on: self.osdRequested.emit(on))
        self._language_menu = add_language_menu(m, self._language, self.languageRequested.emit)
        m.addAction(self.tr("Fenster öffnen")).triggered.connect(lambda _=False: self.showWindowRequested.emit())
        if flat:  # with several devices every submenu restores its own mouse
            m.addAction(self.tr("Originalzustand wiederherstellen")).triggered.connect(
                lambda _=False: self.restore_original())
        m.addSeparator()
        m.addAction(self.tr("Beenden")).triggered.connect(lambda _=False: self.quitRequested.emit())

    def _add_daemon_entries(self, m: QMenu) -> bool:
        """Fill the daemon part of the menu; False if it is one submenu per device
        (several connected mice), True for the flat single-device menu."""
        if not self.client.is_available:
            m.addAction(self.client.unavailable_text()).setEnabled(False)
            return True
        devices = self.client.devices()
        connected = [d for d in devices if d[3]]
        if len(connected) >= 2:
            for model, name, _connection, _up in connected:
                self._add_profiles(m.addMenu(name), model, restore=True)
            return False
        # one connected mouse (or none: show the Naga's state, as before)
        model = connected[0][0] if connected else (devices[0][0] if devices else NAGA_MODEL)
        if not connected and any(d[0] == NAGA_MODEL for d in devices):
            model = NAGA_MODEL
        self._restore_model = model
        self._add_profiles(m, model)
        return True

    def _add_profiles(self, m: QMenu, model: str, restore: bool = False) -> None:
        active = self.client.active_profile(model)
        group = QActionGroup(m)
        group.setExclusive(True)
        profiles = self.client.list_profiles(model)
        names = dict(profiles)
        m.addAction(self.tr("Aktives Profil: %1").replace("%1", names.get(active, active))).setEnabled(False)
        for pid, name in profiles:
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(pid == active)
            group.addAction(a)
            a.triggered.connect(lambda _=False, pid=pid: self.client.activate_profile(pid, model))
        m.addSeparator()
        m.addAction(battery_text(self.client.status(model))).setEnabled(False)
        if restore:
            m.addAction(self.tr("Originalzustand wiederherstellen")).triggered.connect(
                lambda _=False: self.restore_original(model))

    def menu_texts(self) -> list[str]:
        return [a.text() for a in self.menu.actions() if not a.isSeparator()]

    def restore_original(self, model: str | None = None) -> None:
        model = model or self._restore_model
        if not self._confirm(self.tr("Originalzustand wiederherstellen"),
                             self.tr("Die gesicherten Originalwerte werden auf die Maus zurückgeschrieben: "
                                     "DPI, DPI-Stufen, Abfragerate, Helligkeit und Energie-Einstellungen "
                                     "(Leerlaufzeit, Energiesparschwelle). Effekte und Farben gehören nicht "
                                     "dazu – die kommen durch Aus- und wieder Einstecken der Maus zurück. "
                                     "Fortfahren?")):
            return
        try:
            failed = self.client.restore_original(model)
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
