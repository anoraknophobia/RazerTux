"""Main window: profile header, layer toggle and the tab area (Synapse style)."""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QT_TRANSLATE_NOOP, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QMainWindow, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from naga_config.config.model import DefaultAction, Profile, default_profile, validate
from naga_config.config.store import ConfigStore, new_profile_id
from naga_config.gui.action_dialog import ActionDialog
from naga_config.gui.dbus_client import DaemonClient
from naga_config.gui.mouse_view import MouseView
from naga_config.hw.buttons import ButtonId

APP_TITLE = QT_TRANSLATE_NOOP("MainWindow", "Naga-Konfiguration")
_PLACEHOLDERS = [
    (QT_TRANSLATE_NOOP("MainWindow", "Leistung"), QT_TRANSLATE_NOOP("MainWindow", "Kommt in Schritt 9.")),
    (QT_TRANSLATE_NOOP("MainWindow", "Beleuchtung"), QT_TRANSLATE_NOOP("MainWindow", "Kommt in Schritt 10.")),
    (QT_TRANSLATE_NOOP("MainWindow", "Mausrad"), QT_TRANSLATE_NOOP("MainWindow", "Kommt in Schritt 10.")),
    (QT_TRANSLATE_NOOP("MainWindow", "Makros"), QT_TRANSLATE_NOOP("MainWindow", "Kommt in Schritt 11.")),
]


class MainWindow(QMainWindow):
    def __init__(self, store: ConfigStore, client: DaemonClient, parent: QWidget | None = None):
        super().__init__(parent)
        self.store, self.client = store, client
        self.hide_on_close = False  # set by the app when a tray icon is running
        self.layer = "standard"
        self.macros = store.load_macros()
        self.profile: Profile = default_profile()
        self._baseline: dict = {}
        self._active_id = ""
        self._daemon_errors: list[str] = []
        self._current_errors: list[str] = []
        self._dismissed_errors: list[str] = []
        self._build()
        self.client.available.connect(self._on_available)
        self.client.statusChanged.connect(self._on_status)
        self.client.profileChanged.connect(self._on_active_changed)
        self.reload_profile_list()
        self._active_id = client.active_profile() or store.load_settings().active_profile
        self.select_profile(self._active_id, force=True)
        self._on_status(client.status())
        self._on_available(client.is_available)

    # -- construction --------------------------------------------------------
    def _build(self) -> None:
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(180)
        self.profile_combo.activated.connect(lambda _i: self.select_profile(self.profile_combo.currentData()))
        header = QHBoxLayout()
        header.addWidget(QLabel(self.tr("Profil:")))
        header.addWidget(self.profile_combo)
        for text, slot in [(self.tr("Neu"), self.new_profile), (self.tr("Umbenennen"), self.rename_profile),
                           (self.tr("Duplizieren"), self.duplicate_profile), (self.tr("Löschen"), self.delete_profile),
                           (self.tr("Export …"), self.export_profile), (self.tr("Import …"), self.import_profile)]:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=slot: s())
            header.addWidget(b)
        self.activate_btn = QPushButton(self.tr("Aktivieren"))
        self.activate_btn.setToolTip(self.tr("Dieses Profil für die Maus verwenden"))
        self.activate_btn.clicked.connect(lambda _=False: self.activate_current())
        header.addWidget(self.activate_btn)
        header.addStretch(1)
        self.layer_buttons = {}
        group = QButtonGroup(self)
        for key, text in (("standard", self.tr("Standard")), ("hypershift", self.tr("Hypershift"))):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(key == "standard")
            b.clicked.connect(lambda _=False, k=key: self.set_layer(k))
            group.addButton(b)
            self.layer_buttons[key] = b
            header.addWidget(b)
        self.battery_label = QLabel()
        header.addWidget(self.battery_label)

        self.banner = self._make_banner(self.tr(
            "Der Dienst (naga-configd) läuft nicht. Änderungen werden gespeichert, "
            "aber erst nach dem Start des Dienstes angewendet."))
        self.openrazer_banner = self._make_banner(self.tr(
            "OpenRazer-Treiber ist geladen – bitte deinstallieren, sonst stören sich beide Programme"))
        self.errors_banner = self._make_banner("", "#8a5a00")
        self.errors_dismiss = QPushButton(self.tr("Ausblenden"), self.errors_banner)
        self.errors_dismiss.clicked.connect(lambda _=False: self._dismiss_errors())
        self.errors_banner.layout().addWidget(self.errors_dismiss)

        self.mouse_view = MouseView()
        self.mouse_view.bindingClicked.connect(self.edit_binding)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.mouse_view, self.tr("Tasten"))
        for name, text in _PLACEHOLDERS:
            label = QLabel(self.tr(text))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabs.addTab(label, self.tr(name))

        self.save_btn = QPushButton(self.tr("Speichern"))
        self.save_btn.clicked.connect(lambda _=False: self.save())
        self.revert_btn = QPushButton(self.tr("Verwerfen"))
        self.revert_btn.clicked.connect(lambda _=False: self.revert())
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self.revert_btn)
        footer.addWidget(self.save_btn)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(header)
        for b in (self.banner, self.openrazer_banner, self.errors_banner):
            lay.addWidget(b)
        lay.addWidget(self.tabs, 1)
        lay.addLayout(footer)
        self.setCentralWidget(central)
        self.resize(1000, 760)

    def _make_banner(self, text: str, colour: str = "#b3261e") -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ background: {colour}; }} QLabel {{ color: white; }}")
        row = QHBoxLayout(frame)
        frame.label = QLabel(text)  # type: ignore[attr-defined]
        frame.label.setWordWrap(True)  # type: ignore[attr-defined]
        row.addWidget(frame.label, 1)  # type: ignore[attr-defined]
        frame.hide()
        return frame

    # -- hooks (replaced in tests) --------------------------------------------
    def _warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def _ask_unsaved(self) -> str:
        box = QMessageBox(QMessageBox.Icon.Question, self.tr(APP_TITLE),
                          self.tr("Das Profil hat ungespeicherte Änderungen."), parent=self)
        save = box.addButton(self.tr("Speichern"), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(self.tr("Verwerfen"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(self.tr("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return {save: "save", discard: "discard"}.get(box.clickedButton(), "cancel")

    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    # -- dirty tracking -------------------------------------------------------
    @property
    def is_dirty(self) -> bool:
        return self.profile.to_dict() != self._baseline

    def _update_dirty(self) -> None:
        dirty = self.is_dirty
        self.setWindowTitle(f"{self.tr(APP_TITLE)} – {self.profile.name}{' *' if dirty else ''}")
        self.save_btn.setEnabled(dirty)
        self.revert_btn.setEnabled(dirty)
        self.activate_btn.setEnabled(self.profile.id != self._active_id and self.client.is_available)

    def _resolve_unsaved(self) -> bool:
        """True if it is OK to leave the current state (saved or discarded)."""
        if not self.is_dirty:
            return True
        answer = self._ask_unsaved()
        if answer == "save":
            return self.save()
        return answer == "discard"

    # -- profile list / switching --------------------------------------------
    def reload_profile_list(self, select: str | None = None) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for p in self.store.list_profiles():
            self.profile_combo.addItem(p.name, p.id)
        self.profile_combo.blockSignals(False)
        if select is not None:
            self.profile_combo.setCurrentIndex(max(self.profile_combo.findData(select), 0))

    def select_profile(self, profile_id: str, force: bool = False) -> bool:
        if not force and profile_id != self.profile.id and not self._resolve_unsaved():
            self.profile_combo.setCurrentIndex(max(self.profile_combo.findData(self.profile.id), 0))
            return False
        self._show_profile(self.store.load_profile(profile_id))
        return True

    def _show_profile(self, profile: Profile) -> None:
        self.profile = profile
        self._baseline = copy.deepcopy(profile.to_dict())
        if self.profile_combo.findData(profile.id) < 0:
            self.reload_profile_list()
        self.profile_combo.setCurrentIndex(max(self.profile_combo.findData(profile.id), 0))
        self._refresh()

    def revert(self) -> None:
        self._show_profile(self.store.load_profile(self.profile.id))

    def _refresh(self) -> None:
        self.macros = self.store.load_macros()
        layer = self.profile.standard if self.layer == "standard" else self.profile.hypershift
        self.mouse_view.set_bindings(layer, self.macros)
        self._update_warnings()
        self._update_dirty()

    # -- editing --------------------------------------------------------------
    def set_layer(self, layer: str) -> None:
        self.layer = layer
        self.layer_buttons[layer].setChecked(True)
        self._refresh()

    def set_action(self, button: ButtonId, action) -> None:
        if self.layer == "standard":
            self.profile.standard[button] = action
        elif isinstance(action, DefaultAction):
            self.profile.hypershift.pop(button, None)  # missing == default
        else:
            self.profile.hypershift[button] = action
        self._refresh()

    def edit_binding(self, button: ButtonId) -> None:
        layer = self.profile.standard if self.layer == "standard" else self.profile.hypershift
        self.macros = self.store.load_macros()
        action = ActionDialog.pick(self, layer.get(button, DefaultAction()), self.macros,
                                   button=button, layer=self.layer)
        if action is not None:
            self.set_action(button, action)

    # -- saving ---------------------------------------------------------------
    def save(self) -> bool:
        errors = validate(self.profile, self.store.load_macros())
        if errors:
            self._warn(self.tr("Speichern nicht möglich"),
                       self.tr("Das Profil enthält Fehler:") + "\n\n" + "\n".join(f"• {e}" for e in errors))
            return False
        try:
            self.store.save_profile(self.profile)
        except OSError as e:
            self._warn(self.tr("Speichern fehlgeschlagen"),
                       self.tr("Das Profil konnte nicht geschrieben werden:") + f"\n{e}")
            return False
        self._baseline = copy.deepcopy(self.profile.to_dict())
        self.client.reload()
        self.reload_profile_list(select=self.profile.id)
        self._update_dirty()
        return True

    def activate_current(self) -> None:
        if self._resolve_unsaved():
            self.client.activate_profile(self.profile.id)

    # -- profile operations ---------------------------------------------------
    def _ask_name(self, title: str, default: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, self.tr("Name des Profils:"), text=default)
        return name.strip() if ok and name.strip() else None

    def _add_profile(self, profile: Profile) -> None:
        self.store.save_profile(profile)
        self.reload_profile_list()
        self._show_profile(profile)
        self.client.reload()

    def new_profile(self) -> None:
        name = self._ask_name(self.tr("Neues Profil")) if self._resolve_unsaved() else None
        if name:
            self._add_profile(Profile(id=new_profile_id(name), name=name,
                                      standard=default_profile().standard))

    def duplicate_profile(self) -> None:
        name = self._ask_name(self.tr("Profil duplizieren"),
                                    self.tr("%1 (Kopie)").replace("%1", self.profile.name)) \
            if self._resolve_unsaved() else None
        if name:
            copy_ = Profile.from_dict(self.profile.to_dict())
            copy_.id, copy_.name = new_profile_id(name), name
            self._add_profile(copy_)

    def rename_profile(self) -> None:
        name = self._ask_name(self.tr("Profil umbenennen"), self.profile.name)
        if name:
            self.profile.name = name
            self.profile_combo.setItemText(self.profile_combo.currentIndex(), name)
            self._update_dirty()

    def delete_profile(self) -> None:
        if len(self.store.list_profiles()) < 2:
            self._warn(self.tr("Löschen nicht möglich"), self.tr("Das letzte Profil kann nicht gelöscht werden."))
            return
        if not self._confirm(self.tr("Profil löschen"),
                             self.tr("Profil „%1“ wirklich löschen?").replace("%1", self.profile.name)):
            return
        self.store.delete_profile(self.profile.id)
        self.reload_profile_list()
        self.select_profile(self.profile_combo.itemData(0), force=True)
        self.client.reload()

    def export_profile(self) -> None:
        if not self._resolve_unsaved():
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Profil exportieren"),
                                              f"{self.profile.name}.json", self.tr("Profil (*.json)"))
        if path:
            try:
                self.store.export_profile(self.profile.id, Path(path))
            except (OSError, ValueError) as e:
                self._warn(self.tr("Export fehlgeschlagen"),
                           self.tr("Das Profil konnte nicht exportiert werden:") + f"\n{e}")

    def import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Profil importieren"), "", self.tr("Profil (*.json)"))
        if path:
            self.import_profile_from(Path(path))

    def import_profile_from(self, path: Path) -> None:
        if not self._resolve_unsaved():
            return
        try:
            profile = self.store.import_profile(path)
        except Exception as e:  # corrupt JSON, wrong structure, unreadable file
            self._warn(self.tr("Import fehlgeschlagen"),
                       self.tr("Die Datei „%1“ ist kein gültiges Profil und wurde nicht importiert.")
                       .replace("%1", path.name) + f"\n\n{e}")
            return
        self.reload_profile_list()
        self._show_profile(profile)
        self.client.reload()

    # -- daemon state ---------------------------------------------------------
    def _on_available(self, up: bool) -> None:
        self.banner.setVisible(not up)
        self._update_dirty()

    def _on_active_changed(self, profile_id: str) -> None:
        self._active_id = profile_id
        self._update_dirty()

    def _update_warnings(self) -> None:
        """Config errors from the daemon and from our own store (dismissible)."""
        lines = list(dict.fromkeys([*map(str, self._daemon_errors), *self.store.errors]))
        self.errors_banner.label.setText(self.tr("Konfigurationsprobleme:") + "\n"
                                         + "\n".join(f"• {e}" for e in lines))
        self.errors_banner.setVisible(bool(lines) and lines != self._dismissed_errors)
        self._current_errors = lines

    def _dismiss_errors(self) -> None:
        self._dismissed_errors = list(self._current_errors)
        self.errors_banner.hide()

    def _on_status(self, status: dict) -> None:
        self._daemon_errors = list(status.get("config_errors", [])) if status else []
        self.openrazer_banner.setVisible(bool(status and status.get("openrazer_loaded")))
        self._update_warnings()
        battery = int(status.get("battery", -1)) if status else -1
        if battery >= 0:
            text = self.tr("Akku: %1 %").replace("%1", str(battery))
            if status.get("charging"):
                text += " " + self.tr("(lädt)")
            self.battery_label.setText(text)
        else:
            self.battery_label.setText(self.tr("Akku: –") if status and status.get("connected") else "")

    # -- closing --------------------------------------------------------------
    def request_close(self) -> bool:
        """Ask about unsaved changes; True if closing/quitting may go on."""
        if not self._resolve_unsaved():
            return False
        if self.is_dirty:  # discarded
            self.revert()
        return True

    def closeEvent(self, e: QCloseEvent) -> None:
        if not self.request_close():
            e.ignore()
        elif self.hide_on_close:
            e.ignore()
            self.hide()
        else:
            e.accept()
