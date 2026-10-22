"""Main window: profile header, layer toggle and the tab area (Synapse style)."""
from __future__ import annotations

import copy

from PySide6.QtCore import QT_TRANSLATE_NOOP, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QTabWidget, QVBoxLayout,
    QWidget,
)

from razertux.config.model import DefaultAction, Profile, default_profile, validate
from razertux.config.store import ConfigStore
from razertux.gui.action_dialog import ActionDialog
from razertux.gui.buttons_tab import ButtonsTab
from razertux.gui.dbus_client import DaemonClient
from razertux.gui.header import HeaderBar
from razertux.gui.language import (
    add_language_menu, check_language, make_language_combo, select_language,
)
from razertux.gui.lighting_tab import LightingTab
from razertux.gui.macro_tab import MacroTab, strip_macro
from razertux.gui.performance_tab import PerformanceTab
from razertux.gui.power_tab import PowerTab
from razertux.gui.profile_actions import ProfileActions
from razertux.gui.scroll_tab import ScrollTab
from razertux.hw.buttons import ButtonId

APP_TITLE = QT_TRANSLATE_NOOP("MainWindow", "Naga-Konfiguration")
POWER_FIELDS = ("low_battery_notify", "idle_time", "low_battery_threshold",  # GUI-owned settings
                "scroll_stage_names")


class MainWindow(QMainWindow, ProfileActions):
    themeRequested = Signal(str)  # "synapse" | "system"
    languageRequested = Signal(str)  # Settings.language

    def __init__(self, store: ConfigStore, client: DaemonClient, parent: QWidget | None = None):
        super().__init__(parent)
        self.store, self.client = store, client
        self.hide_on_close = False  # set by the app when a tray icon is running
        self.layer = "standard"
        self.macros = store.load_macros()
        self.settings = store.load_settings()
        self._settings_baseline = self._power_dict()
        self.profile: Profile = default_profile()
        self._baseline: dict = {}
        self._macros_baseline: dict = self._macro_dicts()
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
        self.header = HeaderBar({
            "new": self.new_profile, "rename": self.rename_profile, "duplicate": self.duplicate_profile,
            "delete": self.delete_profile, "export": self.export_profile, "import": self.import_profile,
            "activate": self.activate_current})
        self.profile_combo, self.activate_btn = self.header.profile_combo, self.header.activate_btn
        self.dpi_label, self.battery_label = self.header.dpi_label, self.header.battery_label
        self.profile_combo.activated.connect(lambda _i: self.select_profile(self.profile_combo.currentData()))

        self.banner = self._make_banner(self.tr(
            "Der Dienst (naga-configd) läuft nicht. Änderungen werden gespeichert, "
            "aber erst nach dem Start des Dienstes angewendet."))
        self.openrazer_banner = self._make_banner(self.tr(
            "OpenRazer-Treiber ist geladen – bitte deinstallieren, sonst stören sich beide Programme"))
        self.errors_banner = self._make_banner("", "#8a5a00")
        self.errors_dismiss = QPushButton(self.tr("Ausblenden"), self.errors_banner)
        self.errors_dismiss.clicked.connect(lambda _=False: self._dismiss_errors())
        self.errors_banner.layout().addWidget(self.errors_dismiss)

        self.buttons_tab = ButtonsTab()
        self.buttons_tab.bindingClicked.connect(self.edit_binding)
        self.buttons_tab.layerChanged.connect(self.set_layer)
        self.buttons_tab.remapToggled.connect(self.set_remap)
        self.mouse_view = self.buttons_tab.mouse_view
        self.performance_tab, self.lighting_tab, self.power_tab = PerformanceTab(), LightingTab(), PowerTab()
        self.scroll_tab = ScrollTab()
        for tab in (self.performance_tab, self.lighting_tab, self.power_tab, self.scroll_tab):
            tab.changed.connect(self._update_dirty)
        self.macro_tab = MacroTab(self.macros, self._all_profiles)
        self.macro_tab.changed.connect(self._refresh)
        self.macro_tab.macroDeleted.connect(self._on_macro_deleted)
        self.tabs = QTabWidget()
        for widget, name in ((self.buttons_tab, self.tr("TASTEN")), (self.performance_tab, self.tr("LEISTUNG")),
                             (self.scroll_tab, self.tr("MAUSRAD")), (self.lighting_tab, self.tr("BELEUCHTUNG")),
                             (self.power_tab, self.tr("ENERGIE")), (self.macro_tab, self.tr("MAKROS"))):
            self.tabs.addTab(widget, name)
        self.power_tab.load(self.settings)
        self.scroll_tab.load(self.settings)
        view = self.menuBar().addMenu(self.tr("Ansicht"))
        self.system_colors_action = view.addAction(self.tr("Systemfarben verwenden"))
        self.system_colors_action.setCheckable(True)
        self.system_colors_action.triggered.connect(
            lambda on: self.themeRequested.emit("system" if on else "synapse"))
        self.language_menu = add_language_menu(view, self.settings.language, self.languageRequested.emit)

        self.save_btn = QPushButton(self.tr("Speichern"))
        self.save_btn.clicked.connect(lambda _=False: self.save())
        self.revert_btn = QPushButton(self.tr("Verwerfen"))
        self.revert_btn.clicked.connect(lambda _=False: self.revert())
        self.language_combo = make_language_combo(self.settings.language, self.languageRequested.emit)
        footer = QHBoxLayout()
        footer.setContentsMargins(16, 8, 16, 12)
        footer.addWidget(self.language_combo)
        footer.addStretch(1)
        footer.addWidget(self.revert_btn)
        footer.addWidget(self.save_btn)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        for b in (self.banner, self.openrazer_banner, self.errors_banner):
            lay.addWidget(b)
        body = QVBoxLayout()
        body.setContentsMargins(16, 4, 16, 0)
        body.addWidget(self.tabs)
        lay.addLayout(body, 1)
        lay.addLayout(footer)
        self.setCentralWidget(central)
        self.resize(1100, 800)

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
        return (self.profile.to_dict() != self._baseline or self._macro_dicts() != self._macros_baseline
                or self._power_dict() != self._settings_baseline)

    def _power_dict(self) -> dict:
        return copy.deepcopy({f: getattr(self.settings, f) for f in POWER_FIELDS})

    def _reload_settings(self) -> None:
        self.settings = self.store.load_settings()
        self._settings_baseline = self._power_dict()
        self.power_tab.load(self.settings)
        self.scroll_tab.load(self.settings)

    def set_theme_checked(self, name: str) -> None:
        self.system_colors_action.setChecked(name == "system")

    def set_language_checked(self, name: str) -> None:
        check_language(self.language_menu, name)
        select_language(self.language_combo, name)

    def _macro_dicts(self) -> dict:
        return {k: m.to_dict() for k, m in self.macros.items()}

    def _reload_macros(self) -> None:
        """Replace the working copy (in place, the tab shares the dict) by the saved one."""
        saved = self.store.load_macros()
        self.macros.clear()
        self.macros.update(saved)
        self._macros_baseline = self._macro_dicts()
        if hasattr(self, "macro_tab"):
            self.macro_tab.refresh()

    def _all_profiles(self) -> list[Profile]:
        return [self.profile if p.id == self.profile.id else p for p in self.store.list_profiles()]

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
        if answer == "discard":
            self._reload_macros()
            self._reload_settings()
            return True
        return False

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
        self.performance_tab.load(profile)
        self.lighting_tab.load(profile)
        self.buttons_tab.set_remap(profile.remap_top_buttons)
        self._refresh()

    def revert(self) -> None:
        self._reload_macros()
        self._reload_settings()
        self._show_profile(self.store.load_profile(self.profile.id))

    def _refresh(self) -> None:
        layer = self.profile.standard if self.layer == "standard" else self.profile.hypershift
        self.mouse_view.set_bindings(layer, self.macros, top_locked=not self.profile.remap_top_buttons)
        self._update_warnings()
        self._update_dirty()

    # -- editing --------------------------------------------------------------
    def set_layer(self, layer: str) -> None:
        self.layer = layer
        self.buttons_tab.set_layer(layer)
        self._refresh()

    def set_remap(self, on: bool) -> None:
        self.profile.remap_top_buttons = on
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
        action = ActionDialog.pick(self, layer.get(button, DefaultAction()), self.macros,
                                   button=button, layer=self.layer)
        if action is not None:
            self.set_action(button, action)

    # -- saving ---------------------------------------------------------------
    def save(self) -> bool:
        self.macro_tab.commit_recording()
        errors = validate(self.profile, self.macros)
        if errors:
            self._warn(self.tr("Speichern nicht möglich"),
                       self.tr("Das Profil enthält Fehler:") + "\n\n" + "\n".join(f"• {e}" for e in errors))
            return False
        try:
            if self._macro_dicts() != self._macros_baseline:
                self.store.save_macros(self.macros)
                self._macros_baseline = self._macro_dicts()
            if self._power_dict() != self._settings_baseline:
                on_disk = self.store.load_settings()  # keep active_profile/theme as they are now
                for f in POWER_FIELDS:
                    setattr(on_disk, f, getattr(self.settings, f))
                self.store.save_settings(on_disk)
                self._settings_baseline = self._power_dict()
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

    def _on_macro_deleted(self, macro_id: str) -> None:
        """Deleted macro: reset its bindings in every profile and drop it from disk.

        Other unsaved macro edits stay unsaved (only this id leaves disk, memory and baseline)."""
        try:
            for p in self.store.list_profiles():  # on-disk state (the open profile too)
                strip_macro(p, macro_id)
                self.store.save_profile(p)
            on_disk = self.store.load_macros()
            on_disk.pop(macro_id, None)
            self.store.save_macros(on_disk)
        except OSError as e:
            self._warn(self.tr("Speichern fehlgeschlagen"),
                       self.tr("Das Makro konnte nicht gelöscht werden:") + f"\n{e}")
            return
        strip_macro(self.profile, macro_id)  # unsaved edits stay, only the binding goes
        base = Profile.from_dict(self._baseline)
        strip_macro(base, macro_id)
        self._baseline = base.to_dict()
        self._macros_baseline.pop(macro_id, None)
        self.client.reload()
        self._refresh()

    def activate_current(self) -> None:
        if self._resolve_unsaved():
            self.client.activate_profile(self.profile.id)

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
        self.header.set_status(status)
        self.scroll_tab.set_status(status)

    # -- closing --------------------------------------------------------------
    def request_close(self) -> bool:
        """Ask about unsaved changes; True if closing/quitting may go on."""
        self.macro_tab.commit_recording()
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
