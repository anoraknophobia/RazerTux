"""Customize view like Synapse: the mouse pictures in the middle, the binding labels
left (side keys) and right (clicks, wheel, top buttons), each connected to its key
by a thin leader line. Hovering a label or a key lights up both; clicking a key
does the same as clicking its label."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import QPushButton, QWidget

from naga_config.config.model import Action, DefaultAction, Macro
from naga_config.gui.labels import action_label, button_name, firmware_top_label
from naga_config.gui.mouse_scene import SIDE_IDS, SIDE_SVG, TOP_IDS, TOP_SVG, Drawing, themed_svg
from naga_config.hw.buttons import ButtonId

__all__ = ["MouseView", "themed_svg"]

SIDE_BUTTONS = [ButtonId(f"side_{n}") for n in range(1, 13)]
TOP_BUTTONS = (ButtonId.TOP_1, ButtonId.TOP_2)
OTHER_BUTTONS = [ButtonId.LEFT, ButtonId.RIGHT, ButtonId.WHEEL_UP, ButtonId.MIDDLE,
                 ButtonId.WHEEL_DOWN, ButtonId.WHEEL_LEFT, ButtonId.WHEEL_RIGHT,
                 ButtonId.TOP_1, ButtonId.TOP_2]
WHEEL_BUTTONS = (ButtonId.MIDDLE, ButtonId.WHEEL_UP, ButtonId.WHEEL_DOWN,
                 ButtonId.WHEEL_LEFT, ButtonId.WHEEL_RIGHT)


def element_of(b: ButtonId) -> str:
    """SVG element id of a button (all wheel functions share the wheel)."""
    return "wheel" if b in WHEEL_BUTTONS else b.value


# where a leader line ends inside its element (fractions of the element rect);
# side keys: at the left edge, the line runs below the keys in front of it
_ANCHOR = {**{b: (0.16, 0.5) for b in SIDE_BUTTONS},
           ButtonId.LEFT: (0.55, 0.3), ButtonId.RIGHT: (0.5, 0.3),
           # wheel: one dot each along its right edge, in label order (no crossings)
           ButtonId.WHEEL_UP: (0.78, 0.14), ButtonId.MIDDLE: (0.78, 0.34),
           ButtonId.WHEEL_DOWN: (0.78, 0.54), ButtonId.WHEEL_LEFT: (0.78, 0.72),
           ButtonId.WHEEL_RIGHT: (0.78, 0.88)}
# the button a click on a shared element opens
_PRIMARY = {"wheel": ButtonId.MIDDLE}

LINE = QColor("#555555")
DOT = QColor("#8a8a8a")
GAP = 56          # space between a label column and the pictures (room for the lines)
DOT_GAP = 8       # label text -> start dot
TOP_SCALE = 0.8   # top view height relative to the side view


class MouseView(QWidget):
    bindingClicked = Signal(object)  # ButtonId

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._buttons: dict[ButtonId, QPushButton] = {}
        self._texts: dict[ButtonId, str] = {}
        self._tips: dict[ButtonId, str] = {}
        self._hot: ButtonId | None = None
        self.side = Drawing(SIDE_SVG, SIDE_IDS, keys_layer=True)
        self.top = Drawing(TOP_SVG, TOP_IDS)
        for b in SIDE_BUTTONS:
            self._make(b, "right")
        for b in OTHER_BUTTONS:
            self._make(b, "left")
        self.setMouseTracking(True)
        self.setMinimumSize(760, 400)
        self._load()

    def _make(self, b: ButtonId, align: str) -> None:
        btn = QPushButton(self)
        btn.setObjectName("bindingLabel")
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("QPushButton { text-align: %s; padding: 3px 4px; }" % align)
        btn.clicked.connect(lambda _=False, b=b: self.bindingClicked.emit(b))
        btn.installEventFilter(self)
        self._buttons[b] = btn
        self._texts[b] = ""

    # -- bindings ---------------------------------------------------------------
    def button_text(self, b: ButtonId) -> str:
        return self._texts[b]

    def set_bindings(self, actions: dict[ButtonId, Action], macros: dict[str, Macro],
                     layout: str | None = None, top_locked: bool = False) -> None:
        """top_locked: the firmware owns the top buttons (normal device mode); their
        labels show what the mouse does and are greyed out, not clickable."""
        for b, btn in self._buttons.items():
            a = actions.get(b, DefaultAction())
            locked = top_locked and b in TOP_BUTTONS
            label = firmware_top_label(b) if locked else action_label(a, b, macros, layout)
            btn.setEnabled(not locked)
            self._tips[b] = (self.tr("Belegbar, wenn „Obere Tasten frei belegen“ an ist")
                             if locked else "")
            if b in SIDE_BUTTONS:
                self._texts[b] = f"{button_name(b)}  →  {label}"
            else:
                self._texts[b] = f"{button_name(b)}:  {label}"
            font = btn.font()
            font.setBold(not locked and not isinstance(a, DefaultAction))
            btn.setFont(font)
        if self._hot is not None and not self._buttons[self._hot].isEnabled():
            self.set_hot(None)
        self._relayout()

    # -- geometry ---------------------------------------------------------------
    def drawing_of(self, b: ButtonId) -> Drawing:
        return self.side if b in SIDE_BUTTONS else self.top

    def key_rect(self, b: ButtonId) -> QRectF:
        """Rectangle of the button's key on the drawing (widget coordinates)."""
        return self.drawing_of(b).element_rect(element_of(b))

    def label_rect(self, b: ButtonId) -> QRectF:
        return QRectF(self._buttons[b].geometry())

    def leader_line(self, b: ButtonId) -> list[QPointF]:
        """Start dot (next to the label), elbow, end dot (inside the key)."""
        label, key = self.label_rect(b), self.key_rect(b)
        fx, fy = _ANCHOR.get(b, (0.5, 0.5))
        end = QPointF(key.left() + key.width() * fx, key.top() + key.height() * fy)
        y = label.center().y()
        if b in SIDE_BUTTONS:
            start = QPointF(label.right() + DOT_GAP, y)
            elbow = QPointF(self.side.rect.left() - GAP * 0.35, y)
        else:
            start = QPointF(label.left() - DOT_GAP, y)
            elbow = QPointF(self.top.rect.right() + GAP * 0.35, y)
        return [start, elbow, end]

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        cap = max(160, int(w * 0.3))
        widths = {}
        for b, btn in self._buttons.items():
            fm = btn.fontMetrics()
            text = self._texts[b]
            shown = fm.elidedText(text, Qt.TextElideMode.ElideRight, cap - 12)
            btn.setText(shown)
            tip = self._tips.get(b, "")
            btn.setToolTip("\n".join(t for t in (text if shown != text else "", tip) if t))
            widths[b] = min(cap, fm.horizontalAdvance(shown) + 12)
        left_w = max(widths[b] for b in SIDE_BUTTONS)
        right_w = max(widths[b] for b in OTHER_BUTTONS)
        area = QRectF(left_w + GAP, 8, max(10, w - left_w - right_w - 2 * GAP), max(10, h - 16))
        side_a, top_a, spacing = self.side.aspect(), self.top.aspect(), 28
        side_h = min(area.height(), (area.width() - spacing) / (side_a + top_a * TOP_SCALE))
        top_h = side_h * TOP_SCALE
        total_w = side_h * side_a + spacing + top_h * top_a
        x0 = area.left() + (area.width() - total_w) / 2
        cy = area.center().y()
        self.side.place(QRectF(x0, cy - side_h / 2, side_h * side_a, side_h))
        self.top.place(QRectF(x0 + side_h * side_a + spacing, cy - top_h / 2, top_h * top_a, top_h))
        row_h = max(btn.sizeHint().height() for btn in self._buttons.values())
        self._stack(SIDE_BUTTONS, self.side.rect, row_h, lambda b: QRectF(
            self.side.rect.left() - GAP - widths[b], 0, widths[b], row_h))
        self._stack(OTHER_BUTTONS, self.top.rect, row_h, lambda b: QRectF(
            self.top.rect.right() + GAP, 0, widths[b], row_h))
        self.update()

    def _stack(self, buttons: list[ButtonId], picture: QRectF, row_h: int, place) -> None:
        """Spread a label column evenly over the picture's height."""
        pitch = min(max(row_h + 4, (picture.height() * 0.9) / len(buttons)), row_h * 1.9)
        y0 = picture.center().y() - pitch * len(buttons) / 2 + (pitch - row_h) / 2
        for i, b in enumerate(buttons):
            r = place(b)
            r.moveTop(y0 + i * pitch)
            self._buttons[b].setGeometry(r.toRect())

    # -- hover / click ----------------------------------------------------------
    @property
    def hot(self) -> ButtonId | None:
        return self._hot

    def set_hot(self, b: ButtonId | None) -> None:
        if b == self._hot:
            return
        for old_new in (self._hot, b):
            if old_new is not None:
                btn = self._buttons[old_new]
                btn.setProperty("hot", old_new == b)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        self._hot = b
        self._load()
        self.update()

    def button_at(self, pos: QPointF) -> ButtonId | None:
        """The (enabled) button whose key is under `pos`."""
        for drawing in (self.side, self.top):
            element = drawing.element_at(pos)
            if element is not None:
                b = _PRIMARY[element] if element in _PRIMARY else ButtonId(element)
                return b if self._buttons[b].isEnabled() else None
        return None

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if e.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            for b, btn in self._buttons.items():
                if btn is obj:
                    self.set_hot(b if e.type() == QEvent.Type.Enter and btn.isEnabled() else None)
        return super().eventFilter(obj, e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        b = self.button_at(e.position())
        self.set_hot(b)
        self.setCursor(Qt.CursorShape.PointingHandCursor if b else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, e) -> None:
        self.set_hot(None)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            b = self.button_at(e.position())
            if b is not None:
                self.bindingClicked.emit(b)

    # -- painting ---------------------------------------------------------------
    def _load(self) -> None:
        hot = element_of(self._hot) if self._hot is not None else None
        self.side.load(self.palette(), hot)
        self.top.load(self.palette(), hot)

    def changeEvent(self, e) -> None:
        if e.type() in (e.Type.PaletteChange, e.Type.ApplicationPaletteChange):
            self._load()
            self.update()
        super().changeEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_dots(p)
        self.side.paint(p)
        self.top.paint(p)
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        lines = {b: self.leader_line(b) for b in self._buttons}
        order = sorted(lines, key=lambda b: b == self._hot)  # the hot line on top
        for b in order:
            colour = accent if b == self._hot else QColor(LINE)
            if not self._buttons[b].isEnabled():
                colour.setAlpha(110)
            p.setPen(QPen(colour, 1.4 if b == self._hot else 1.0))
            p.drawPolyline(lines[b])
        self.side.paint_keys(p)
        p.setPen(Qt.PenStyle.NoPen)
        for b in order:
            p.setBrush(accent if b == self._hot else DOT)
            for pt in (lines[b][0], lines[b][-1]):
                p.drawEllipse(pt, 2.5, 2.5)

    def _paint_dots(self, p: QPainter) -> None:
        """Faint dot grid behind the pictures (like Synapse)."""
        base = self.palette().color(QPalette.ColorRole.Window)
        if base.lightness() >= 110:
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 14))
        step = 22
        for x in range(step // 2, self.width(), step):
            for y in range(step // 2, self.height(), step):
                p.drawEllipse(QPointF(x, y), 1.0, 1.0)
