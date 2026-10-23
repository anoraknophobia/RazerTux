"""Macro tab: macro list, step editor and recorder (edits the window's working copy)."""
from __future__ import annotations

from collections.abc import Callable

from evdev import ecodes as E
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from razertux.config.model import (
    ButtonStep, DefaultAction, DelayStep, KeyStep, Macro, MacroAction, MacroStep, Profile,
)
from razertux.config.store import new_macro_id
from razertux.gui.action_dialog import KeyCapture
from razertux.gui.keynames import key_label
from razertux.gui.macro_recorder import (
    MAX_DELAY_MS, MAX_STEPS, MacroRecorder, RecordField, apply_fixed_delay,
)

_BUTTONS = [E.BTN_LEFT, E.BTN_RIGHT, E.BTN_MIDDLE, E.BTN_SIDE, E.BTN_EXTRA]


def profiles_using(profiles: list[Profile], macro_id: str) -> list[str]:
    """Names of the profiles that bind `macro_id` in any layer."""
    return [p.name for p in profiles
            if any(isinstance(a, MacroAction) and a.macro_id == macro_id
                   for layer in (p.standard, p.hypershift) for a in layer.values())]


def strip_macro(p: Profile, macro_id: str) -> None:
    """Reset every binding of `macro_id` to the default (hypershift: missing == default)."""
    for layer in (p.standard, p.hypershift):
        for b, a in list(layer.items()):
            if isinstance(a, MacroAction) and a.macro_id == macro_id:
                if layer is p.hypershift:
                    del layer[b]
                else:
                    layer[b] = DefaultAction()


class MacroTab(QWidget):
    changed = Signal()
    macroDeleted = Signal(str)  # id; the window rewrites the affected profiles

    def __init__(self, macros: dict[str, Macro], profiles: Callable[[], list[Profile]],
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.macros, self._profiles = macros, profiles
        self.recorder = MacroRecorder(self)
        self.recorder.stepsChanged.connect(self._live_update)
        self.recorder.limitReached.connect(self._on_limit)
        self.recorder.focusLost.connect(self.commit_recording)
        self._build()
        self.refresh()

    # -- hooks (replaced in tests) -------------------------------------------
    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def _ask_name(self, title: str, default: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, self.tr("Macro name:"), text=default)
        return name.strip() if ok and name.strip() else None

    def _ask_key(self) -> int | None:
        return self._dialog(self.tr("Insert key"), KeyCapture(),
                            lambda w: w.codes[0] if w.codes else None)

    def _ask_button(self) -> int | None:
        combo = QComboBox()
        for c in _BUTTONS:
            combo.addItem(key_label(c), c)
        return self._dialog(self.tr("Insert mouse button"), combo, lambda w: w.currentData())

    def _ask_delay(self, current: int = 50) -> int | None:
        spin = QSpinBox()
        spin.setRange(1, MAX_DELAY_MS)
        spin.setSuffix(" ms")
        spin.setValue(current)
        return self._dialog(self.tr("Delay"), spin, lambda w: w.value())

    def _dialog(self, title: str, widget: QWidget, result: Callable):
        d = QDialog(self)
        d.setWindowTitle(title)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Apply"))
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("Cancel"))
        box.accepted.connect(d.accept)
        box.rejected.connect(d.reject)
        lay = QVBoxLayout(d)
        lay.addWidget(widget)
        lay.addWidget(box)
        return result(widget) if d.exec() == QDialog.DialogCode.Accepted else None

    # -- construction ---------------------------------------------------------
    def _button(self, text: str, slot: Callable) -> QPushButton:
        b = QPushButton(text)
        b.setAutoDefault(False)
        b.clicked.connect(lambda _=False: slot())
        return b

    def _build(self) -> None:
        self.macro_list = QListWidget()
        self.macro_list.currentRowChanged.connect(lambda _r: self._show_steps())
        left = QVBoxLayout()
        left.addWidget(QLabel(self.tr("Macros")))
        left.addWidget(self.macro_list, 1)
        row = QHBoxLayout()
        for text, slot in ((self.tr("New"), self.new_macro), (self.tr("Rename"), self.rename_macro),
                           (self.tr("Delete"), self.delete_macro)):
            row.addWidget(self._button(text, slot))
        left.addLayout(row)

        self.step_list = QListWidget()
        self.step_list.itemDoubleClicked.connect(lambda _i: self.edit_delay())
        edit = QHBoxLayout()
        for text, slot in ((self.tr("Key …"), self.add_key), (self.tr("Mouse button …"), self.add_button),
                           (self.tr("Delay …"), self.add_delay), (self.tr("Edit delay …"), self.edit_delay),
                           (self.tr("Delete step"), self.delete_step),
                           (self.tr("Up"), lambda: self.move_step(-1)),
                           (self.tr("Down"), lambda: self.move_step(1))):
            edit.addWidget(self._button(text, slot))

        self.record_field = RecordField()
        self.record_field.setMinimumHeight(48)
        self.record_field.installEventFilter(self.recorder)
        self.record_btn = self._button("", self.toggle_recording)
        self.record_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # clicking it must not steal field focus
        self.delays_check = QCheckBox(self.tr("Record delays"))
        self.delays_check.setChecked(True)
        self.fixed_check = QCheckBox(self.tr("Fixed delays:"))
        self.fixed_spin = QSpinBox()
        self.fixed_spin.setRange(1, MAX_DELAY_MS)
        self.fixed_spin.setSuffix(" ms")
        self.fixed_spin.setValue(10)
        self.apply_btn = self._button(self.tr("Apply to all delays"), self.apply_fixed_to_all)
        opts = QHBoxLayout()
        for w in (self.delays_check, self.fixed_check, self.fixed_spin, self.apply_btn):
            opts.addWidget(w)
        opts.addStretch(1)
        self.hint = QLabel()
        self.hint.setWordWrap(True)

        right = QVBoxLayout()
        self.title = QLabel()
        right.addWidget(self.title)
        right.addWidget(self.step_list, 1)
        right.addLayout(edit)
        right.addWidget(QLabel(self.tr("Recording (only in the field below):")))
        right.addWidget(self.record_field)
        right.addWidget(self.record_btn)
        right.addLayout(opts)
        right.addWidget(self.hint)
        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addLayout(right, 3)
        self._set_recording_ui(False)

    # -- model helpers --------------------------------------------------------
    @property
    def current_id(self) -> str | None:
        item = self.macro_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    @property
    def current(self) -> Macro | None:
        return self.macros.get(self.current_id) if self.current_id else None

    def refresh(self, select: str | None = None) -> None:
        """Rebuild lists from the (possibly replaced) macro dict."""
        keep = select or self.current_id
        self.commit_recording()
        self.macro_list.blockSignals(True)
        self.macro_list.clear()
        for mid, m in self.macros.items():
            it = QListWidgetItem(m.name)
            it.setData(Qt.ItemDataRole.UserRole, mid)
            self.macro_list.addItem(it)
            if mid == keep:
                self.macro_list.setCurrentItem(it)
        if self.macro_list.currentRow() < 0 and self.macro_list.count():
            self.macro_list.setCurrentRow(0)
        self.macro_list.blockSignals(False)
        self._show_steps()

    def step_text(self, s: MacroStep) -> str:
        arrow = "↓" if getattr(s, "down", True) else "↑"
        if isinstance(s, DelayStep):
            return self.tr("Delay %1 ms").replace("%1", str(s.ms))
        kind = self.tr("Key") if isinstance(s, KeyStep) else self.tr("Mouse button")
        return f"{kind} {key_label(s.code)} {arrow}"

    def _show_steps(self, select: int | None = None) -> None:
        m = self.current
        self.title.setText(self.tr("Steps of “%1” (%2)").replace("%1", m.name)
                           .replace("%2", str(len(m.steps))) if m else self.tr("No macro selected."))
        self.step_list.clear()
        for s in (m.steps if m else []):
            self.step_list.addItem(self.step_text(s))
        if select is not None and 0 <= select < self.step_list.count():
            self.step_list.setCurrentRow(select)
        self.record_btn.setEnabled(m is not None or self.recorder.recording)

    def _changed(self, select: int | None = None) -> None:
        self._show_steps(select)
        self.changed.emit()

    # -- macro list -----------------------------------------------------------
    def add_macro(self, name: str) -> str:
        mid = new_macro_id(name)
        while mid in self.macros:
            mid = new_macro_id(name)
        self.macros[mid] = Macro(mid, name, [])
        self.refresh(select=mid)
        self.changed.emit()
        return mid

    def new_macro(self) -> None:
        name = self._ask_name(self.tr("New macro"))
        if name:
            self.add_macro(name)

    def rename_macro(self) -> None:
        m = self.current
        name = self._ask_name(self.tr("Rename macro"), m.name) if m else None
        if m and name:
            m.name = name
            self.macro_list.currentItem().setText(name)
            self._changed()

    def delete_macro(self) -> bool:
        mid, m = self.current_id, self.current
        if not m:
            return False
        used = profiles_using(self._profiles(), mid)
        text = self.tr("Really delete macro “%1”?").replace("%1", m.name)
        if used:
            text += "\n\n" + self.tr("It is used in these profiles:") + "\n" \
                + "\n".join(f"• {n}" for n in used) \
                + "\n\n" + self.tr("Its assignments will be reset to “Default”.")
        if not self._confirm(self.tr("Delete macro"), text):
            return False
        del self.macros[mid]
        self.refresh()
        self.macroDeleted.emit(mid)
        self.changed.emit()
        return True

    # -- step editing ---------------------------------------------------------
    def _insert(self, steps: list[MacroStep]) -> None:
        m = self.current
        if not m:
            return
        if len(m.steps) + len(steps) > MAX_STEPS:
            self.hint.setText(self.tr("A macro can have at most %1 steps.").replace("%1", str(MAX_STEPS)))
            return
        self.hint.setText("")
        pos = self.step_list.currentRow() + 1 if self.step_list.currentRow() >= 0 else len(m.steps)
        m.steps[pos:pos] = steps
        self._changed(pos + len(steps) - 1)

    def insert_key(self, code: int) -> None:
        self._insert([KeyStep(code, True), KeyStep(code, False)])

    def insert_button(self, code: int) -> None:
        self._insert([ButtonStep(code, True), ButtonStep(code, False)])

    def insert_delay(self, ms: int) -> None:
        self._insert([DelayStep(ms)])

    def add_key(self) -> None:
        if (code := self._ask_key()) is not None:
            self.insert_key(code)

    def add_button(self) -> None:
        if (code := self._ask_button()) is not None:
            self.insert_button(code)

    def add_delay(self) -> None:
        if (ms := self._ask_delay()) is not None:
            self.insert_delay(ms)

    def set_delay(self, index: int, ms: int) -> None:
        m = self.current
        if m and 0 <= index < len(m.steps) and isinstance(m.steps[index], DelayStep):
            m.steps[index] = DelayStep(min(max(ms, 1), MAX_DELAY_MS))
            self._changed(index)

    def edit_delay(self) -> None:
        m, i = self.current, self.step_list.currentRow()
        if m and i >= 0 and isinstance(m.steps[i], DelayStep):
            ms = self._ask_delay(m.steps[i].ms)
            if ms is not None:
                self.set_delay(i, ms)

    def delete_step(self) -> None:
        m, i = self.current, self.step_list.currentRow()
        if m and i >= 0:
            del m.steps[i]
            self._changed(min(i, len(m.steps) - 1))

    def move_step(self, delta: int) -> None:
        m, i = self.current, self.step_list.currentRow()
        j = i + delta
        if m and i >= 0 and 0 <= j < len(m.steps):
            m.steps[i], m.steps[j] = m.steps[j], m.steps[i]
            self._changed(j)

    def apply_fixed_to_all(self) -> None:
        m = self.current
        if m:
            m.steps = apply_fixed_delay(m.steps, self.fixed_spin.value())
            self._changed()

    # -- recording ------------------------------------------------------------
    def _set_recording_ui(self, on: bool) -> None:
        self.record_btn.setText(self.tr("Stop recording") if on else self.tr("Start recording"))
        self.record_field.setText(self.tr("Recording – type or click here now …") if on
                                  else self.tr("Recording field (start recording, then type here)"))
        for w in (self.delays_check, self.fixed_check, self.fixed_spin, self.macro_list, self.step_list):
            w.setEnabled(not on)
        if on:
            self.record_field.setFocus()

    def start_recording(self) -> None:
        if not self.current:
            return
        self.hint.setText("")
        self._base_steps = list(self.current.steps)  # recording appends to these
        fixed = self.fixed_spin.value() if self.fixed_check.isChecked() else None
        room = MAX_STEPS - len(self._base_steps)
        self.recorder.start(self.delays_check.isChecked(), fixed, max_steps=room)
        self._set_recording_ui(True)

    def stop_recording(self) -> None:
        m = self.current
        steps = self.recorder.stop()
        self._set_recording_ui(False)
        if m is not None:
            m.steps = self._base_steps + steps
            self._changed(len(m.steps) - 1)

    def commit_recording(self) -> None:
        """Finish a running recording and keep its steps (focus loss, save, close)."""
        if self.recorder.recording:
            self.stop_recording()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.ActivationChange and self.recorder.recording \
                and not self.window().isActiveWindow():
            self.commit_recording()

    def toggle_recording(self) -> None:
        self.stop_recording() if self.recorder.recording else self.start_recording()

    def _live_update(self) -> None:
        m = self.current
        if m is not None and self.recorder.recording:
            self.step_list.clear()
            for s in self._base_steps + self.recorder.steps:
                self.step_list.addItem(self.step_text(s))
            self.step_list.scrollToBottom()

    def _on_limit(self) -> None:
        self.stop_recording()
        self.hint.setText(self.tr("Maximum length of %1 steps reached – recording stopped.")
                          .replace("%1", str(MAX_STEPS)))
