"""Records keyboard/mouse input inside the recording field into macro steps."""
from __future__ import annotations

from evdev import ecodes as E
from PySide6.QtCore import QElapsedTimer, QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QPushButton, QWidget

from naga_config.config.model import ButtonStep, DelayStep, KeyStep, MacroStep
from naga_config.gui.keynames import keycode_from_qt

MAX_STEPS = 500
MAX_DELAY_MS = 60000
_QT_BUTTONS = {
    Qt.MouseButton.LeftButton: E.BTN_LEFT, Qt.MouseButton.RightButton: E.BTN_RIGHT,
    Qt.MouseButton.MiddleButton: E.BTN_MIDDLE, Qt.MouseButton.BackButton: E.BTN_SIDE,
    Qt.MouseButton.ForwardButton: E.BTN_EXTRA,
}


def apply_fixed_delay(steps: list[MacroStep], ms: int) -> list[MacroStep]:
    """Copy of `steps` with every pause set to `ms`."""
    return [DelayStep(ms) if isinstance(s, DelayStep) else s for s in steps]


class RecordField(QPushButton):
    """Focusable field that receives the input to record (Tab stays recordable)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def focusNextPrevChild(self, next_: bool) -> bool:
        return False


class MacroRecorder(QObject):
    limitReached = Signal()
    stepsChanged = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.recording = False
        self._reset()

    def _reset(self) -> None:
        self._steps: list[MacroStep] = []
        self._down: set[tuple[type, int]] = set()
        self._timer = QElapsedTimer()
        self._record_delays = True
        self._fixed_ms: int | None = None
        self._limit = MAX_STEPS
        self._finalized = True

    # -- control --------------------------------------------------------------
    def start(self, record_delays: bool = True, fixed_ms: int | None = None,
              max_steps: int = MAX_STEPS) -> None:
        """Begin a fresh recording (all per-recording state is reset)."""
        self._reset()
        self._record_delays, self._fixed_ms, self._limit = record_delays, fixed_ms, max_steps
        self.recording, self._finalized = True, False

    def stop(self) -> list[MacroStep]:
        if not self._finalized:
            for kind, code in sorted(self._down, key=lambda t: (t[0].__name__, t[1])):  # release what is still held
                self._steps.append(kind(code, False))  # room was reserved
            self._down.clear()
            self._finalized = True
        self.recording = False
        return list(self._steps)

    @property
    def steps(self) -> list[MacroStep]:
        return list(self._steps)

    # -- input ----------------------------------------------------------------
    def key_event(self, code: int, down: bool) -> None:
        self._event(KeyStep, code, down)

    def button_event(self, code: int, down: bool) -> None:
        self._event(ButtonStep, code, down)

    def _event(self, kind: type, code: int, down: bool) -> None:
        if not self.recording or code <= 0:
            return
        if down == ((kind, code) in self._down):  # auto-repeat / stray release
            return
        needed = 1 + (1 if self._steps and self._record_delays else 0)
        held_after = len(self._down) + (1 if down else -1)  # releases we must still fit
        if len(self._steps) + needed + held_after > self._limit:
            self.recording = False
            self.limitReached.emit()
            return
        if self._steps and self._record_delays:
            elapsed = self._timer.elapsed() if self._timer.isValid() else 1
            ms = self._fixed_ms if self._fixed_ms else elapsed
            self._steps.append(DelayStep(min(max(int(ms), 1), MAX_DELAY_MS)))
        self._timer.restart()
        self._steps.append(kind(code, down))
        (self._down.add if down else self._down.discard)((kind, code))
        self.stepsChanged.emit()

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if not self.recording:
            return False
        t = e.type()
        if t == QEvent.Type.ShortcutOverride:
            e.accept()
            return True
        if t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if not e.isAutoRepeat():
                self.key_event(keycode_from_qt(e), t == QEvent.Type.KeyPress)
            return True
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            code = _QT_BUTTONS.get(e.button())
            if code:
                self.button_event(code, t == QEvent.Type.MouseButtonPress)
            return True
        return False
