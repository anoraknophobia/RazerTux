"""Recorder wizard: the laser picture of a mouse and the button recording page (guided steps for
mice, then free recording; live "Recording…" indicator; auto-stop after 120 s)."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QPointF, QRectF, Qt, QTimer, Signal
from evdev import ecodes as E
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from razertux.gui.labels import button_name
from razertux.gui.layouts import Layout, by_name, layout_for
from razertux.gui.mouse_scene import Drawing
from razertux.gui.recorder_session import guarded
from razertux.hw.buttons import ButtonId
from razertux.recorder.recording import (
    AUTO_STOP, TEMPLATE_CHOICES, GuidedSteps, Recording, Step, load_template,
)

FIRST = (ButtonId.LEFT, ButtonId.RIGHT, ButtonId.MIDDLE, ButtonId.WHEEL_UP, ButtonId.WHEEL_DOWN,
         ButtonId.WHEEL_LEFT, ButtonId.WHEEL_RIGHT)  # asked first, whatever the label order
SETTLE_MS = 700  # after the first signal of a step: further signals still belong to it
TICK_MS = 250
# a press on one of the wizard's own buttons with the recorded mouse arrives as BTN_LEFT: those
# within CLICK_GUARD seconds after the press are dropped; BTN_LEFT is held HOLD_MS back so a
# press Qt reports a little later than the evdev signal drops it too
CLICK_GUARD = 0.3
HOLD_MS = 150
_HINTS = {
    ButtonId.WHEEL_UP: QT_TRANSLATE_NOOP("ButtonsPage", "Scroll the wheel up"),
    ButtonId.WHEEL_DOWN: QT_TRANSLATE_NOOP("ButtonsPage", "Scroll the wheel down"),
    ButtonId.WHEEL_LEFT: QT_TRANSLATE_NOOP("ButtonsPage", "Tilt the wheel left"),
    ButtonId.WHEEL_RIGHT: QT_TRANSLATE_NOOP("ButtonsPage", "Tilt the wheel right"),
}


def _tr(text: str) -> str:
    return QCoreApplication.translate("ButtonsPage", text)


class LaserPicture(QWidget):
    """The views of a layout side by side, laser look, one key lit up."""

    def __init__(self, layout: Layout | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.drawings: dict[str, Drawing] = {}
        self._layout: Layout | None = None
        self._hot: ButtonId | None = None
        self.setMinimumSize(160, 140)
        if layout is not None:
            self.set_layout(layout)

    def set_layout(self, layout: Layout | None) -> None:
        self._layout, self._hot = layout, None
        self.drawings = ({v.name: Drawing(v, layout.elements(v.name)) for v in layout.views}
                         if layout is not None else {})
        self.update()

    def set_hot(self, button: ButtonId | None) -> None:
        self._hot = button
        self.update()

    def paintEvent(self, _e) -> None:
        if self._layout is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        spec = self._layout.buttons.get(self._hot) if self._hot is not None else None
        views = self._layout.views
        for v in views:
            self.drawings[v.name].load(self.palette(), spec.element if spec and spec.view == v.name else None)
        gap = 16 * (len(views) - 1)
        aspects = [self.drawings[v.name].aspect() * v.scale for v in views]
        area = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        h = min(area.height(), (area.width() - gap) / sum(aspects))
        x = area.center().x() - (h * sum(aspects) + gap) / 2
        for v in views:
            d = self.drawings[v.name]
            vh = h * v.scale
            d.place(QRectF(x, area.center().y() - vh / 2, vh * d.aspect(), vh))
            d.paint(p)
            x += vh * d.aspect() + 16
        if spec is not None:  # a ring around the key asked for
            r = self.drawings[spec.view].element_rect(spec.element)
            if not r.isEmpty():
                p.setPen(QColor(255, 255, 255, 160))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(r.center(), r.width() / 2 + 7, r.height() / 2 + 7)
                p.drawEllipse(QPointF(r.center()), 2.0, 2.0)


def guided_plan(session) -> tuple[str, list[Step] | None, Layout | None]:
    """(template, guided steps or None for free recording only, picture layout)."""
    if session.kind != "mouse":
        return "free", None, None
    if session.info is not None:
        layout = layout_for(session.info)
        pairs = [(b, s.element) for b, s in layout.buttons.items()]
        template = layout.template
    else:
        template = session.template_choice or "generic"
        layout = by_name(TEMPLATE_CHOICES[template])
        pairs = load_template(template)
    first = [p for b in FIRST for p in pairs if p[0] == b]
    return template, GuidedSteps(first + [p for p in pairs if p[0] not in FIRST]), layout


class ButtonsPage(QWidget):
    changed = Signal()

    def __init__(self, session, settle_ms: int = SETTLE_MS, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.phase = "idle"  # idle | guided | free | stopping | done
        self.recording: Recording | None = None
        self.template, self.steps, self._gen, self._t0 = "free", None, 0, 0.0
        self._results: dict[str, dict] = {}
        self._end_all, self._ending, self._settle_index = False, "", -1
        self._held: list[tuple[float, list]] = []  # BTN_LEFT signals waiting HOLD_MS
        self.title = self.tr("Buttons")
        self.picture = LaserPicture()
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.step_label = QLabel()
        self.step_label.setObjectName("cardTitle")
        self.step_label.setWordWrap(True)
        self.caught = QLabel()
        self.caught.setObjectName("mutedText")
        self.caught.setWordWrap(True)
        self.indicator = QLabel()
        self.indicator.setObjectName("recIndicator")
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.signal_list = QListWidget()  # free recording: what arrived so far
        for lab in (self.intro, self.step_label, self.caught, self.message):
            lab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.start_btn = QPushButton(self.tr("Start recording"))
        self.absent_btn = QPushButton(self.tr("Not on this device (N)"))
        self.nosignal_btn = QPushButton(self.tr("Pressed, nothing happened (Enter)"))
        self.stop_btn = QPushButton(self.tr("Stop recording"))
        self.finish_btn = QPushButton(self.tr("Done"))
        self.absent_btn.setShortcut("N")
        self.nosignal_btn.setShortcut("Return")
        self.start_btn.clicked.connect(lambda _=False: self.start())
        self.absent_btn.clicked.connect(lambda _=False: self._manual("absent"))
        self.nosignal_btn.clicked.connect(lambda _=False: self._manual("no_signal"))
        self.stop_btn.clicked.connect(lambda _=False: self.stop(end_all=True))
        self.finish_btn.clicked.connect(lambda _=False: self.stop(end_all=True))
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self.tick)
        self.settle = QTimer(self)
        self.settle.setSingleShot(True)
        self.settle.setInterval(settle_ms)
        self.settle.timeout.connect(self._settled)
        session.signalled.connect(self._on_signal)
        session.wizardPressed.connect(self._held.clear)
        self.hold = QTimer(self)
        self.hold.setSingleShot(True)
        self.hold.timeout.connect(self._release_held)
        row = QHBoxLayout()
        for b in (self.start_btn, self.absent_btn, self.nosignal_btn, self.stop_btn, self.finish_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.indicator)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(self.intro)
        lay.addWidget(self.picture, 1)
        lay.addWidget(self.step_label)
        lay.addWidget(self.caught)
        lay.addWidget(self.signal_list, 1)
        lay.addWidget(self.message)
        lay.addLayout(row)
        self._show()

    # -- wizard hooks ----------------------------------------------------------------
    def can_next(self) -> bool:
        return self.phase == "done"

    def enter(self) -> None:
        if self.phase != "done":
            self.template, self.steps, layout = guided_plan(self.session)
            self.picture.set_layout(layout)
            self._show()
        self.session.pause()

    def leave(self) -> None:
        if self.phase in ("guided", "free", "stopping"):
            self._gen += 1  # results of the running listen are dropped
            self.session.stop_listen()
            self.timer.stop()
            self.settle.stop()
            self.phase = "idle"
            self._show()
        self.hold.stop()
        self._held.clear()
        self.session.resume()

    # -- recording ---------------------------------------------------------------------
    @property
    def step_index(self) -> int:
        if self.recording is None or self.recording.steps is None:
            return 0
        cur = self.recording.current
        return self.recording.steps.index(cur) if cur is not None else len(self.recording.steps)

    @guarded
    def start(self) -> None:
        self._results, self._end_all = {}, False
        self.message.setText("")
        self.session.pause()
        self._begin("guided" if self.steps else "free")

    def _begin(self, phase: str) -> None:
        self.phase = phase
        self.recording = Recording(self.session.kind, self.template,
                                   self.steps if phase == "guided" else None, clock=self.session.clock)
        self.recording.start()
        self._t0 = self.session.clock()
        self._gen += 1
        gen = self._gen
        self.session.listen(lambda ans: self._listen_done(gen, ans), lambda err: self._listen_failed(gen, err))
        self.timer.start()
        self._show()

    @guarded
    def _on_signal(self, sig: list) -> None:
        if self.phase not in ("guided", "free") or self.recording is None or len(sig) < 3:
            return
        if int(sig[1]) == E.EV_KEY and int(sig[2]) == E.BTN_LEFT:
            now = time.monotonic()
            if now - self.session.last_press < CLICK_GUARD:
                return  # the click on a wizard button
            self._held.append((now, sig))
            if not self.hold.isActive():
                self.hold.start(HOLD_MS)
            return
        self._take(sig)

    @guarded
    def _release_held(self) -> None:
        now = time.monotonic()
        due = [sig for t, sig in self._held if now - t >= HOLD_MS / 1000 - 0.005]
        self._held[:] = [(t, sig) for t, sig in self._held if now - t < HOLD_MS / 1000 - 0.005]
        for sig in due:
            if self.phase in ("guided", "free"):
                self._take(sig)
        if self._held:
            self.hold.start(max(1, int(HOLD_MS - (now - self._held[0][0]) * 1000)))

    def _take(self, sig: list) -> None:
        sign = sig[3] if len(sig) > 3 else None
        if not self.recording.record(str(sig[0]), int(sig[1]), int(sig[2]), sign):
            return
        if self.phase == "guided" and not self.settle.isActive():
            self._settle_index = self.step_index
            self.settle.start()
        self._show()

    @guarded
    def _settled(self) -> None:
        if self.phase == "guided" and self.step_index == self._settle_index:
            self.recording.next()
            self._after_step()

    @guarded
    def _manual(self, what: str) -> None:
        if self.phase != "guided":
            return
        self.settle.stop()
        getattr(self.recording, what)()
        self._after_step()

    def _after_step(self) -> None:
        if self.recording.finished:
            self.stop(end_all=False)
        else:
            self._show()

    @guarded
    def tick(self) -> None:
        if self.phase not in ("guided", "free"):
            return
        if self.recording.expired:
            self.stop(end_all=self.phase == "free")
        else:
            self._show()

    @guarded
    def stop(self, end_all: bool) -> None:
        """End the running phase; the next one (free after guided) starts when the listen ended."""
        if self.phase not in ("guided", "free"):
            return
        self.settle.stop()
        self.timer.stop()
        self._end_all = end_all or self.phase == "free"
        self._ending = self.phase
        self.phase = "stopping"
        self._show()
        self.session.stop_listen()

    @guarded
    def _listen_done(self, gen: int, answer) -> None:
        if gen != self._gen:
            return
        std = answer.get("standard_keys_seen", 0) if isinstance(answer, dict) else 0
        ending = self._ending if self.phase == "stopping" else self.phase
        self._results[ending] = self.recording.finish(std)
        if ending == "guided" and not self._end_all:
            self.session.refresh_pause()  # a fresh daemon deadline for the second listen
            self._begin("free")
        else:
            self._finalize()

    @guarded
    def _listen_failed(self, gen: int, err: str) -> None:
        if gen != self._gen:
            return
        self._on_slot_error(err)
        self.session.resume()  # never leave the daemon paused after an error

    def _on_slot_error(self, err: str) -> None:
        """Listen failed or a slot raised: stop everything, keep what was recorded."""
        self._gen += 1
        self.session.stop_listen()
        self.timer.stop()
        self.settle.stop()
        self.hold.stop()
        self._held.clear()
        try:
            if self.recording is not None and self.phase in ("guided", "stopping"):
                self._results.setdefault("guided", self.recording.finish(0))
            self._finalize()
        except Exception:  # noqa: BLE001 - the recording itself is broken: report nothing
            self.session.buttons = {"template": self.template}
            self.phase = "done"
            self._show()
        self.message.setText(self.tr("Recording failed: %1").replace("%1", err))

    def _finalize(self) -> None:
        g, f = self._results.get("guided", {}), self._results.get("free", {})
        results = g.get("results", {})
        known = {n for v in results.values() if isinstance(v, list) for n in v}
        out = {"template": self.template, "results": results, "duplicates": g.get("duplicates", []),
               "signals": [s for s in f.get("signals", []) if s not in known],
               "standard_keys_seen": max(g.get("standard_keys_seen", 0), f.get("standard_keys_seen", 0))}
        out.update({k: g[k] for k in ("timed_out", "stopped_at") if k in g})
        self.session.buttons = out
        self.phase = "done"
        self.timer.stop()
        self._show()

    # -- display ---------------------------------------------------------------------------
    def _done_items(self) -> list[str]:
        b = self.session.buttons
        out = []
        for key, sig in b.get("results", {}).items():
            text = self.tr("not on this device") if sig == "absent" else ", ".join(sig) or self.tr("no signal")
            out.append(f"{button_name(ButtonId(key))}: {text}")
        return out + list(b.get("signals", []))

    def _show(self) -> None:
        ph = self.phase
        guided = ph == "guided"
        self.start_btn.setVisible(ph in ("idle", "done"))
        self.start_btn.setText(self.tr("Record again") if ph == "done" else self.tr("Start recording"))
        self.absent_btn.setVisible(guided)
        self.nosignal_btn.setVisible(guided)
        self.stop_btn.setVisible(guided)
        self.finish_btn.setVisible(ph == "free")
        if self.steps:
            self.intro.setText(self.tr(
                "First the buttons of the picture one by one: press the lit-up button once. The "
                "wizard moves on by itself. Then press every other button the device has. Only "
                "which signals arrive is stored – no order, no timing, no typed text.")
            + " " + self.tr("Clicks on this window's own buttons are ignored."))
        else:
            self.intro.setText(self.tr(
                "Press every special button of the device once: macro keys, media keys, profile "
                "and mode buttons, wheels. Ordinary typing keys (letters, digits, Enter …) are "
                "never stored, only counted. Only which signals arrive is stored – no order, no timing.")
            + " " + self.tr("Clicks on this window's own buttons are ignored."))
        self.picture.setVisible(self.picture.drawings != {} and ph in ("idle", "guided"))
        step = self.recording.current if guided and self.recording is not None else None
        self.picture.set_hot(step.button if step else None)
        if step is not None:
            text = _tr(_HINTS[step.button]) if step.button in _HINTS else self.tr(
                "Press the button “%1” (lit up on the picture)").replace("%1", button_name(step.button))
            n = self.tr("Button %1 of %2").replace("%1", str(self.step_index + 1)).replace(
                "%2", str(len(self.steps)))
            self.step_label.setText(f"{n}: {text}")
        elif ph == "free" and self.steps:
            self.step_label.setText(self.tr("Now press every other button once (extra buttons, DPI, profile …)"))
        elif ph == "free":
            self.step_label.setText(self.tr("Press the special buttons now"))
        elif ph == "stopping":
            self.step_label.setText(self.tr("Stopping …"))
        elif ph == "done":
            self.step_label.setText(self.tr("Recording finished."))
        else:
            self.step_label.setText(self.tr("Press “Start recording” when you are ready."))
        caught = self.recording.caught if ph in ("guided", "free") and self.recording else []
        self.caught.setText(self.tr("Received: %1").replace("%1", ", ".join(caught)) if caught and guided else "")
        self.caught.setVisible(guided)
        items = self._done_items() if ph == "done" else caught
        self.signal_list.setVisible(ph in ("free", "stopping", "done"))
        if [self.signal_list.item(i).text() for i in range(self.signal_list.count())] != items:
            self.signal_list.clear()
            self.signal_list.addItems(items)
        if ph in ("guided", "free"):
            left = max(0, math.ceil(AUTO_STOP - (self.session.clock() - self._t0)))
            self.indicator.setText(self.tr("Recording… %1").replace("%1", f"{left // 60:02d}:{left % 60:02d}"))
        else:
            self.indicator.setText("")
        self.changed.emit()
