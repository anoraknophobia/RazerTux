"""Customize view like Synapse: the mouse pictures in the middle, the binding labels
left (side keys) and right (clicks, wheel, top buttons), each connected to its key
by a thin leader line. Hovering a label or a key lights up both; clicking a key
does the same as clicking its label."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QWidget

from razertux.config.model import Action, DefaultAction, Macro
from razertux.devices.db import load_devices
from razertux.gui.labels import action_label, button_name, firmware_top_label
from razertux.gui.layouts import Layout, layout_for
from razertux.gui.mouse_scene import Drawing, themed_svg
from razertux.hw.buttons import ButtonId

__all__ = ["MouseView", "themed_svg"]

TOP_BUTTONS = (ButtonId.TOP_1, ButtonId.TOP_2)  # owned by the firmware unless remapped
DEFAULT_MODEL = "naga_v2_pro"  # until the window picks the connected device
ANCHOR = {"left": (0.16, 0.5), "right": (0.84, 0.5)}  # leader line end inside a key by label side
RIGHT_ENTRY = 0.92  # right column: x (fraction of the drawing) where the lines turn horizontal

LINE = QColor("#555555")
DOT = QColor("#8a8a8a")
GAP = 56          # space between a label column and the pictures (room for the lines)
DOT_GAP = 8       # label text -> start dot
SPACING = 28      # between two pictures


class MouseView(QWidget):
    bindingClicked = Signal(object)  # ButtonId

    def __init__(self, layout: Layout | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._buttons: dict[ButtonId, QPushButton] = {}
        self._texts: dict[ButtonId, str] = {}
        self._tips: dict[ButtonId, str] = {}
        self._hot: ButtonId | None = None
        self._pressed: ButtonId | None = None
        self._dots: QPixmap | None = None
        self._cols: dict[str, QRectF] = {}
        self.device_layout: Layout
        self.drawings: dict[str, Drawing] = {}
        self.setMouseTracking(True)
        self.setMinimumSize(760, 400)
        self.set_layout(layout or layout_for(load_devices()[DEFAULT_MODEL], None))

    def set_layout(self, layout: Layout) -> None:
        """Show another mouse: new pictures, one label per button of the layout."""
        for btn in self._buttons.values():
            btn.deleteLater()
        self._buttons, self._texts, self._tips, self._hot, self._pressed = {}, {}, {}, None, None
        self.device_layout = layout
        self.drawings = {v.name: Drawing(v, layout.elements(v.name)) for v in layout.views}
        for b, spec in layout.buttons.items():
            self._make(b, "right" if spec.side == "left" else "left")
        self._load()
        self._relayout()

    def _make(self, b: ButtonId, align: str) -> None:
        btn = QPushButton(self)
        btn.setObjectName("bindingLabel")
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("QPushButton { text-align: %s; padding: 3px 4px; }" % align)
        btn.clicked.connect(lambda _=False, b=b: self.bindingClicked.emit(b))
        btn.installEventFilter(self)
        btn.show()
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
            if self.device_layout.buttons[b].side == "left":
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
    def element_of(self, b: ButtonId) -> str:
        """SVG element id of a button (all wheel functions share the wheel)."""
        return self.device_layout.buttons[b].element

    def drawing_of(self, b: ButtonId) -> Drawing:
        return self.drawings[self.device_layout.buttons[b].view]

    def key_rect(self, b: ButtonId) -> QRectF:
        """Rectangle of the button's key on the drawing (widget coordinates)."""
        return self.drawing_of(b).element_rect(self.element_of(b))

    def label_rect(self, b: ButtonId) -> QRectF:
        return QRectF(self._buttons[b].geometry())

    def leader_line(self, b: ButtonId) -> list[QPointF]:
        """Start dot (next to the label), elbow, entry, end dot (on the key).

        Label -> horizontal to a common elbow column -> straight to an entry column
        just outside the keys -> horizontal into the key. End heights grow in label
        order, so no two lines cross."""
        label = self.label_rect(b)
        y = label.center().y()
        end = self._end_point(b)
        if self.device_layout.buttons[b].side == "left":
            start = QPointF(label.right() + DOT_GAP, y)
            elbow = QPointF(self._cols["left"].left() - GAP * 0.35, y)
            entry_x = min(self.key_rect(k).left() for k in self.device_layout.buttons_on("left")) - 8
        else:
            start = QPointF(label.left() - DOT_GAP, y)
            elbow = QPointF(self._cols["right"].right() + GAP * 0.35, y)
            rect = self.drawing_of(b).rect
            entry_x = rect.left() + rect.width() * RIGHT_ENTRY
        return [start, elbow, QPointF(entry_x, end.y()), end]

    def _end_point(self, b: ButtonId) -> QPointF:
        spec = self.device_layout.buttons[b]
        key = self.key_rect(b)
        fx, fy = spec.anchor or ANCHOR[spec.side]
        return QPointF(key.left() + key.width() * fx, key.top() + key.height() * fy)

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
        columns = {side: self.device_layout.buttons_on(side) for side in ("left", "right")}
        col_w = {side: max((widths[b] for b in bs), default=0) for side, bs in columns.items()}
        margin = {side: col_w[side] + GAP if columns[side] else 8 for side in columns}
        area = QRectF(margin["left"], 8, max(10, w - margin["left"] - margin["right"]), max(10, h - 16))
        views = self.device_layout.views
        aspects = [self.drawings[v.name].aspect() * v.scale for v in views]
        spacing = SPACING * (len(views) - 1)
        base_h = min(area.height(), (area.width() - spacing) / sum(aspects))
        total_w = base_h * sum(aspects) + spacing
        x = area.left() + (area.width() - total_w) / 2
        for v in views:
            d = self.drawings[v.name]
            vh = base_h * v.scale
            vw = vh * d.aspect()
            d.place(QRectF(x, area.center().y() - vh / 2, vw, vh))
            x += vw + SPACING
        row_h = max((btn.sizeHint().height() for btn in self._buttons.values()), default=0)
        for side, bs in columns.items():
            if not bs:
                continue
            union = QRectF()
            for name in dict.fromkeys(self.device_layout.buttons[b].view for b in bs):
                union = union.united(self.drawings[name].rect)
            self._cols[side] = union
            self._stack(bs, union, row_h, lambda b, side=side, union=union: QRectF(
                union.left() - GAP - widths[b] if side == "left" else union.right() + GAP,
                0, widths[b], row_h))
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
        for name, drawing in self.drawings.items():
            element = drawing.element_at(pos)
            if element is not None:
                b = self.device_layout.button_for(name, element)
                return b if b is not None and self._buttons[b].isEnabled() else None
        return None

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        """Hover and keyboard focus on a label light up its key."""
        on = (QEvent.Type.Enter, QEvent.Type.FocusIn)
        if e.type() in (*on, QEvent.Type.Leave, QEvent.Type.FocusOut):
            for b, btn in self._buttons.items():
                if btn is obj:
                    self.set_hot(b if e.type() in on and btn.isEnabled() else None)
        return super().eventFilter(obj, e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        b = self.button_at(e.position())
        self.set_hot(b)
        self.setCursor(Qt.CursorShape.PointingHandCursor if b else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, e) -> None:
        self.set_hot(None)
        super().leaveEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed = self.button_at(e.position())

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        """A click counts only if press and release are on the same key."""
        if e.button() == Qt.MouseButton.LeftButton:
            pressed, self._pressed = self._pressed, None
            b = self.button_at(e.position())
            if b is not None and b == pressed:
                self.bindingClicked.emit(b)

    # -- painting ---------------------------------------------------------------
    def _load(self) -> None:
        hot = self.element_of(self._hot) if self._hot is not None else None
        for drawing in self.drawings.values():
            drawing.load(self.palette(), hot)

    def changeEvent(self, e) -> None:
        if e.type() in (e.Type.PaletteChange, e.Type.ApplicationPaletteChange):
            self._dots = None
            self._load()
            self.update()
        super().changeEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_dots(p)
        for drawing in self.drawings.values():
            drawing.paint(p)
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        lines = {b: self.leader_line(b) for b in self._buttons}
        order = sorted(lines, key=lambda b: b == self._hot)  # the hot line on top
        for b in order:
            colour = accent if b == self._hot else QColor(LINE)
            if not self._buttons[b].isEnabled():
                colour.setAlpha(110)
            p.setPen(QPen(colour, 1.4 if b == self._hot else 1.0))
            p.drawPolyline(lines[b])
        for drawing in self.drawings.values():
            drawing.paint_keys(p)
        p.setPen(Qt.PenStyle.NoPen)
        for b in order:
            p.setBrush(accent if b == self._hot else DOT)
            for pt in (lines[b][0], lines[b][-1]):
                p.drawEllipse(pt, 2.5, 2.5)

    def _paint_dots(self, p: QPainter) -> None:
        """Faint dot grid behind the pictures (like Synapse), cached per size/palette."""
        if self.palette().color(QPalette.ColorRole.Window).lightness() >= 110:
            return
        if self._dots is None or self._dots.size() != self.size():
            pm = QPixmap(self.size())
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor(255, 255, 255, 14))
            step = 22
            for x in range(step // 2, self.width(), step):
                for y in range(step // 2, self.height(), step):
                    q.drawEllipse(QPointF(x, y), 1.0, 1.0)
            q.end()
            self._dots = pm
        p.drawPixmap(0, 0, self._dots)
