"""Language switch (Settings.language): menu entries for window and tray, the
controller that saves the choice, and the restart that applies it.

Texts are translated once at start-up, so a new language needs a restart; the
restart runs `python -m razertux.gui.app` with the original options (the same
code as the installed razertux-gui entry point)."""
from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QObject, QProcess, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QApplication, QComboBox, QMenu, QMessageBox, QWidget

from razertux import i18n
from razertux.config.store import ConfigStore
from razertux.i18n.qt_translator import JsonTranslator

# language names always in their own language; only "System language" is translated
LANGUAGES = (
    ("system", QT_TRANSLATE_NOOP("LanguageMenu", "System language")),
    ("de", "Deutsch"), ("en", "English"), ("es", "Español"), ("fr", "Français"),
)


def _tr(text: str) -> str:
    return QCoreApplication.translate("LanguageMenu", text)


def add_language_menu(parent: QMenu, current: str, pick: Callable[[str], None]) -> QMenu:
    """Submenu "Sprache" with one radio entry per language."""
    menu = parent.addMenu(_tr("Language"))
    group = QActionGroup(menu)
    group.setExclusive(True)
    for key, name in LANGUAGES:
        a = menu.addAction(_tr(name) if key == "system" else name)
        a.setCheckable(True)
        a.setChecked(key == current)
        a.setData(key)
        group.addAction(a)
        a.triggered.connect(lambda _=False, k=key: pick(k))
    return menu


def check_language(menu: QMenu, name: str) -> None:
    for a in menu.actions():
        a.setChecked(a.data() == name)


def make_language_combo(current: str, pick: Callable[[str], None]) -> QComboBox:
    """Compact language dropdown (window footer); same entries as the menu."""
    combo = QComboBox()
    combo.setObjectName("languageCombo")
    combo.setToolTip(_tr("Language"))
    for key, name in LANGUAGES:
        combo.addItem(_tr(name) if key == "system" else name, key)
    select_language(combo, current)
    combo.activated.connect(lambda i: pick(combo.itemData(i)))
    return combo


def select_language(combo: QComboBox, name: str) -> None:
    combo.blockSignals(True)
    combo.setCurrentIndex(max(combo.findData(name), 0))
    combo.blockSignals(False)


def install_language(app: QApplication, store: ConfigStore) -> JsonTranslator:
    """Resolve Settings.language, set it for plain-Python texts and install the
    Qt translator (call before any widget is built)."""
    from PySide6.QtCore import QLocale
    lang = i18n.apply_settings(store.load_settings(), QLocale.system().name())
    store.errors.clear()  # errors of that read come again in the right language
    translator = JsonTranslator(lang, app)
    app.installTranslator(translator)
    return translator


def restart_args(args: list[str], window_visible: bool) -> list[str]:
    """Original options; --tray only if the window is hidden now. --restarted makes the
    new instance wait for this one to quit (only one GUI may run)."""
    args = [a for a in args if a != "--restarted"]
    if window_visible:
        out = [a for a in args if a != "--tray"]
    else:
        out = list(args) if "--tray" in args else [*args, "--tray"]
    return [*out, "--restarted"]


def restart_command(args: list[str]) -> tuple[str, list[str]]:
    return sys.executable, ["-m", "razertux.gui.app", *args]


def start_new_instance(args: list[str]) -> bool:
    """Start the GUI again, detached (the caller quits this one afterwards)."""
    exe, argv = restart_command(args)
    result = QProcess.startDetached(exe, argv)
    return bool(result[0] if isinstance(result, tuple) else result)


class LanguageController(QObject):
    """Single owner of the language setting: window menu and tray call set_language()."""

    changed = Signal(str)
    restartRequested = Signal()

    def __init__(self, store: ConfigStore, dialog_parent: QWidget | None = None,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.store, self._dialog_parent = store, dialog_parent
        self.name = store.load_settings().language

    def _ask_restart(self) -> bool:
        box = QMessageBox(QMessageBox.Icon.Information, self.tr("Language"),
                          self.tr("The language will change after a restart."),
                          parent=self._dialog_parent)
        now = box.addButton(self.tr("Restart now"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.tr("Later"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is now

    def set_language(self, name: str) -> None:
        if name == self.name:
            return
        self.name = name
        s = self.store.load_settings()  # only the language (the daemon owns active_profile)
        s.language = name
        self.store.save_settings(s)
        self.changed.emit(name)
        if self._ask_restart():
            self.restartRequested.emit()
