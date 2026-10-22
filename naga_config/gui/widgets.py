"""Small building blocks for the Synapse-like tabs: cards (panels), toggle switch."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget,
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
