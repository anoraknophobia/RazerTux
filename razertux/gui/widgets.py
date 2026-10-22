"""Small building blocks for the Synapse-like tabs: cards (panels), toggle switch,
pill toggle and the header battery indicator."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QVBoxLayout,
    QWidget,
)


class Card(QFrame):
    """A panel with an UPPERCASE section title (green in the Synapse theme)."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.StyledPanel)  # visible panel with system colours
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(24, 18, 24, 20)
        self.body.setSpacing(10)
        self.title = QLabel(title.upper())
        self.title.setObjectName("cardTitle")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.head = QHBoxLayout()
        self.head.addWidget(self.title)
        self.head.addStretch(1)
        self.body.addLayout(self.head)

    def add(self, item: QWidget | QLayout, stretch: int = 0) -> None:
        if isinstance(item, QLayout):
            self.body.addLayout(item, stretch)
        else:
            self.body.addWidget(item, stretch)


def description(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("cardText")
    label.setWordWrap(True)
    return label


class ToggleSwitch(QCheckBox):
    """Checkbox drawn as an on/off switch with its text on the right."""

    _W, _H = 34, 18

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text()) + 10 if self.text() else 0
        return QSize(self._W + 4 + text_w, max(self._H + 4, fm.height() + 4))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _e) -> None:
        pal = self.palette()
        on = self.isChecked()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, (self.height() - self._H) / 2, self._W, self._H)
        colour = pal.color(QPalette.ColorRole.Highlight if on else QPalette.ColorRole.Mid)
        if not self.isEnabled():
            colour.setAlpha(90)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawRoundedRect(track, self._H / 2, self._H / 2)
        knob = pal.color(QPalette.ColorRole.Window if on else QPalette.ColorRole.WindowText)
        p.setBrush(knob)
        x = track.right() - self._H + 2 if on else track.left() + 2
        p.drawEllipse(QRectF(x, track.top() + 2, self._H - 4, self._H - 4))
        if self.text():
            p.setPen(pal.color(QPalette.ColorGroup.Normal if self.isEnabled()
                               else QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText))
            p.drawText(QRectF(self._W + 10, 0, self.width() - self._W - 10, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class PillToggle(QFrame):
    """Two or more exclusive choices in one rounded pill (Synapse "Standard | Hypershift")."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("pill")
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(3, 3, 3, 3)
        self._row.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

    def add(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("pillButton")
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.group.addButton(b)
        self._row.addWidget(b)
        return b


class BatteryIndicator(QWidget):
    """Header battery: percentage, a drawn battery glyph and a bolt while charging."""

    _GLYPH_W, _GLYPH_H, _BOLT_W = 9, 17, 9

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._text, self.level, self.charging = "", -1, False

    def text(self) -> str:
        return self._text

    def set_state(self, text: str, level: int = -1, charging: bool = False) -> None:
        self._text, self.level, self.charging = text, level, charging
        self.updateGeometry()
        self.update()

    def _glyph_w(self) -> int:
        if self.level < 0:
            return 0
        return 8 + self._GLYPH_W + (self._BOLT_W + 4 if self.charging else 0)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._text) + self._glyph_w() + 2,
                     max(fm.height(), self._GLYPH_H + 2) + 4)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _e) -> None:
        pal = self.palette()
        fg = pal.color(QPalette.ColorRole.WindowText)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_w = self.width() - self._glyph_w()
        p.setPen(fg)
        p.drawText(QRectF(0, 0, text_w, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self._text)
        if self.level < 0:
            return
        x, top = text_w + 8, (self.height() - self._GLYPH_H) / 2
        body = QRectF(x + 0.5, top + 2.5, self._GLYPH_W - 1, self._GLYPH_H - 3)
        p.setPen(QPen(fg, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body, 1.5, 1.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fg)
        p.drawRect(QRectF(x + 2.5, top, self._GLYPH_W - 5, 2))  # terminal
        inner = body.adjusted(2, 2, -2, -2)
        fill = inner.height() * max(0, min(100, self.level)) / 100
        low = self.level <= 15 and not self.charging
        p.setBrush(QColor("#d23c3c") if low else fg)
        p.drawRect(QRectF(inner.left(), inner.bottom() - fill, inner.width(), fill))
        if self.charging:
            bx = x + self._GLYPH_W + 4
            bolt = QPolygonF([QPointF(bx + 5.5, top), QPointF(bx, top + 9.5), QPointF(bx + 4, top + 9.5),
                              QPointF(bx + 3, top + self._GLYPH_H), QPointF(bx + 9, top + 7),
                              QPointF(bx + 5, top + 7), QPointF(bx + 7, top)])
            p.setBrush(pal.color(QPalette.ColorRole.Highlight))
            p.drawPolygon(bolt)
