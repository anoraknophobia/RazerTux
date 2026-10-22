"""Dialog to pick what a button does."""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from razertux.config.model import (
    Action, ComboAction, DefaultAction, DisabledAction, DpiStageAction,
    HypershiftAction, KeyAction, Macro, MacroAction,
)
from razertux.gui.keynames import MODIFIER_CODES, combo_label, key_label, keycode_from_qt
from razertux.gui.labels import WHEEL_BUTTONS, button_name
from razertux.hw.buttons import ButtonId

_TYPES = [
    ("default", QT_TRANSLATE_NOOP("ActionDialog", "Standard")),
    ("disabled", QT_TRANSLATE_NOOP("ActionDialog", "Deaktiviert")),
    ("key", QT_TRANSLATE_NOOP("ActionDialog", "Taste")),
    ("combo", QT_TRANSLATE_NOOP("ActionDialog", "Kombination")),
    ("macro", QT_TRANSLATE_NOOP("ActionDialog", "Makro")),
    ("hypershift", QT_TRANSLATE_NOOP("ActionDialog", "Hypershift-Taste")),
    ("dpi_up", QT_TRANSLATE_NOOP("ActionDialog", "DPI-Stufe hoch")),
    ("dpi_down", QT_TRANSLATE_NOOP("ActionDialog", "DPI-Stufe runter")),
    ("dpi_cycle", QT_TRANSLATE_NOOP("ActionDialog", "DPI-Stufe weiter")),
]
_MODES = [
    ("once", QT_TRANSLATE_NOOP("ActionDialog", "Einmal ausführen")),
    ("repeat_n", QT_TRANSLATE_NOOP("ActionDialog", "n-mal wiederholen")),
    ("while_held", QT_TRANSLATE_NOOP("ActionDialog", "Solange gedrückt")),
    ("toggle", QT_TRANSLATE_NOOP("ActionDialog", "Umschalten (an/aus)")),
]


class KeyCapture(QPushButton):
    """Button that records a key or key combination ("Drücken zum Aufnehmen").

    press()/release() take evdev codes so tests can drive it without events.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.combo_mode = False
        self.codes: list[int] = []
        self._held: set[int] = set()
        self._pressed: list[int] = []
        self._done = True
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._refresh()

    def set_codes(self, codes: list[int]) -> None:
        self.codes = list(codes)
        self._refresh()

    def clear_codes(self) -> None:
        self.codes, self._held, self._pressed, self._done = [], set(), [], True
        self._refresh()

    def _refresh(self) -> None:
        if self.hasFocus() and not self._done:
            self.setText(self.tr("Jetzt Taste drücken …"))
        elif self.codes:
            self.setText(combo_label(self.codes))
        else:
            self.setText(self.tr("Drücken zum Aufnehmen"))
        self.changed.emit()

    def press(self, code: int) -> None:
        if self._done:  # a new recording starts with the first press
            self.codes, self._held, self._pressed, self._done = [], set(), [], False
        self._held.add(code)
        self._pressed.append(code)
        if code not in MODIFIER_CODES:
            mods = [c for c in self._pressed if c in MODIFIER_CODES]
            self.codes = (mods if self.combo_mode else []) + [code]
            self._done = True
        else:
            self.codes = list(self._pressed)
        self._refresh()

    def release(self, code: int) -> None:
        self._held.discard(code)
        if not self._held and not self._done:  # only modifiers were pressed
            if not self.combo_mode:
                self.codes = self.codes[:1]
            self._done = True
            self._refresh()

    def focusNextPrevChild(self, next_: bool) -> bool:  # Tab is recordable
        return False

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.isAutoRepeat():
            return
        self.press(keycode_from_qt(e))
        e.accept()

    def keyReleaseEvent(self, e: QKeyEvent) -> None:
        if not e.isAutoRepeat():
            self.release(keycode_from_qt(e))
        e.accept()

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self._done = True
        self.setText(self.tr("Taste drücken …"))

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self._refresh()


class ActionDialog(QDialog):
    def __init__(self, parent: QWidget | None, current: Action, macros: dict[str, Macro],
                 *, button: ButtonId | None = None, layer: str = "standard"):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Belegung: %1").replace("%1", button_name(button))
                            if button else self.tr("Belegung"))
        self._macros = macros
        allow_hypershift = layer == "standard" and button not in WHEEL_BUTTONS

        self.type_combo = QComboBox()
        for key, text in _TYPES:
            if key != "hypershift" or allow_hypershift:
                self.type_combo.addItem(self.tr(text), key)
        self.capture = KeyCapture()
        self.capture.changed.connect(self._update)
        self.macro_combo = QComboBox()
        for mid, m in macros.items():
            self.macro_combo.addItem(m.name, mid)
        self.mode_combo = QComboBox()
        for key, text in _MODES:
            self.mode_combo.addItem(self.tr(text), key)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 999)
        self.hint = QLabel()
        self.hint.setWordWrap(True)

        form = QFormLayout()
        form.addRow(self.tr("Aktion:"), self.type_combo)
        self._rows = {
            "capture": (QLabel(self.tr("Eingabe:")), self.capture),
            "macro": (QLabel(self.tr("Makro:")), self.macro_combo),
            "mode": (QLabel(self.tr("Modus:")), self.mode_combo),
            "count": (QLabel(self.tr("Anzahl:")), self.count_spin),
        }
        for label, field in self._rows.values():
            form.addRow(label, field)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Übernehmen"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("Abbrechen"))
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.hint)
        lay.addWidget(self.buttons)

        self.type_combo.currentIndexChanged.connect(self._update)
        self.mode_combo.currentIndexChanged.connect(self._update)
        self.macro_combo.currentIndexChanged.connect(self._update)
        self._load(current)
        self._update()

    # -- test/driver helpers -------------------------------------------------
    def select_type(self, key: str) -> None:
        self.type_combo.setCurrentIndex(self.type_combo.findData(key))

    def select_macro(self, macro_id: str) -> None:
        self.macro_combo.setCurrentIndex(self.macro_combo.findData(macro_id))

    def select_mode(self, mode: str) -> None:
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode))

    def _type(self) -> str:
        return self.type_combo.currentData()

    def _load(self, a: Action) -> None:
        match a:
            case DisabledAction():
                self.select_type("disabled")
            case KeyAction(code):
                self.select_type("key")
                self.capture.set_codes([code])
            case ComboAction(codes):
                self.select_type("combo")
                self.capture.set_codes(codes)
            case MacroAction(mid, mode, count):
                self.select_type("macro")
                self.select_macro(mid)
                self.select_mode(mode)
                self.count_spin.setValue(max(count, 1))
            case HypershiftAction():
                self.select_type("hypershift")
            case DpiStageAction(direction):
                self.select_type(f"dpi_{direction}")
            case _:
                self.select_type("default")

    def _update(self, *_: object) -> None:
        t = self._type()
        was_combo = self.capture.combo_mode
        self.capture.combo_mode = t == "combo"
        if was_combo != self.capture.combo_mode:
            self.capture.clear_codes()
        show = {"capture": t in ("key", "combo"), "macro": t == "macro",
                "mode": t == "macro", "count": t == "macro" and self.mode_combo.currentData() == "repeat_n"}
        for name, (label, field) in self._rows.items():
            label.setVisible(show[name])
            field.setVisible(show[name])
        self.hint.setText(self.tr("Noch keine Makros vorhanden (Reiter „Makros“).")
                          if t == "macro" and not self._macros else "")
        self.ok_button.setEnabled(self.result_action() is not None)

    def result_action(self) -> Action | None:
        """The chosen action, or None while the input is incomplete."""
        match self._type():
            case "default":
                return DefaultAction()
            case "disabled":
                return DisabledAction()
            case "hypershift":
                return HypershiftAction()
            case "dpi_up":
                return DpiStageAction("up")
            case "dpi_down":
                return DpiStageAction("down")
            case "dpi_cycle":
                return DpiStageAction("cycle")
            case "key":
                return KeyAction(self.capture.codes[0]) if self.capture.codes else None
            case "combo":
                codes = self.capture.codes
                if not codes:
                    return None
                return ComboAction(list(codes)) if len(codes) > 1 else KeyAction(codes[0])
            case "macro":
                mid = self.macro_combo.currentData()
                if mid is None:
                    return None
                mode = self.mode_combo.currentData()
                return MacroAction(mid, mode, self.count_spin.value() if mode == "repeat_n" else 1)
        return None

    @classmethod
    def pick(cls, parent: QWidget | None, current: Action, macros: dict[str, Macro],
             *, button: ButtonId | None = None, layer: str = "standard") -> Action | None:
        d = cls(parent, current, macros, button=button, layer=layer)
        return d.result_action() if d.exec() == QDialog.DialogCode.Accepted else None
