"""Mouse picture in the middle, binding labels around it (Synapse style)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGridLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from naga_config.config.model import Action, DefaultAction, Macro
from naga_config.gui.labels import action_label, button_name
from naga_config.hw.buttons import ButtonId

ASSETS = Path(__file__).parent / "assets"
SIDE_BUTTONS = [ButtonId(f"side_{n}") for n in range(1, 13)]
OTHER_BUTTONS = [ButtonId.LEFT, ButtonId.RIGHT, ButtonId.MIDDLE, ButtonId.WHEEL_UP,
                 ButtonId.WHEEL_DOWN, ButtonId.WHEEL_LEFT, ButtonId.WHEEL_RIGHT,
                 ButtonId.TOP_1, ButtonId.TOP_2]


def themed_svg(name: str, palette: QPalette) -> bytes:
    """Load an asset and fill its @TOKENS@ with the current palette colours."""
    text = (ASSETS / name).read_text(encoding="utf-8")
    colours = {
        "@FG@": palette.color(QPalette.ColorRole.WindowText).name(),
        "@BG@": palette.color(QPalette.ColorRole.Base).name(),
        "@MUTE@": palette.color(QPalette.ColorRole.Mid).name(),
        "@ACC@": palette.color(QPalette.ColorRole.Highlight).name(),
    }
    for token, value in colours.items():
        text = text.replace(token, value)
    return text.encode("utf-8")


class _Picture(QWidget):
    """An SVG rendered with palette colours (re-rendered on theme change)."""

    def __init__(self, asset: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._asset = asset
        self._renderer = QSvgRenderer(self)
        self._load()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def _load(self) -> None:
        self._renderer.load(QByteArray(themed_svg(self._asset, self.palette())))

    def sizeHint(self):
        return self._renderer.defaultSize()

    def changeEvent(self, e) -> None:
        if e.type() in (e.Type.PaletteChange, e.Type.ApplicationPaletteChange):
            self._load()
            self.update()
        super().changeEvent(e)

    def paintEvent(self, _e) -> None:
        size = self._renderer.defaultSize()
        if size.isEmpty():
            return
        scale = min(self.width() / size.width(), self.height() / size.height())
        w, h = size.width() * scale, size.height() * scale
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._renderer.render(p, QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h))


class MouseView(QWidget):
    bindingClicked = Signal(object)  # ButtonId

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._buttons: dict[ButtonId, QPushButton] = {}
        left, right = QVBoxLayout(), QVBoxLayout()
        for b in SIDE_BUTTONS:
            left.addWidget(self._make(b, Qt.AlignmentFlag.AlignRight))
        left.addStretch(1)
        for b in OTHER_BUTTONS:
            right.addWidget(self._make(b, Qt.AlignmentFlag.AlignLeft))
        right.addStretch(1)
        centre = QVBoxLayout()
        centre.addWidget(_Picture("naga_v2_pro_side.svg"), 3)
        centre.addWidget(_Picture("naga_v2_pro_top.svg"), 2)
        grid = QGridLayout(self)
        grid.addLayout(left, 0, 0)
        grid.addLayout(centre, 0, 1)
        grid.addLayout(right, 0, 2)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 2)

    def _make(self, b: ButtonId, align: Qt.AlignmentFlag) -> QPushButton:
        btn = QPushButton()
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("QPushButton { text-align: %s; padding: 4px 8px; }"
                          % ("right" if align == Qt.AlignmentFlag.AlignRight else "left"))
        btn.clicked.connect(lambda _=False, b=b: self.bindingClicked.emit(b))
        self._buttons[b] = btn
        return btn

    def button_text(self, b: ButtonId) -> str:
        return self._buttons[b].text()

    def set_bindings(self, actions: dict[ButtonId, Action], macros: dict[str, Macro],
                     layout: str | None = None) -> None:
        for b, btn in self._buttons.items():
            a = actions.get(b, DefaultAction())
            label = action_label(a, b, macros, layout)
            if b in SIDE_BUTTONS:
                btn.setText(f"{button_name(b)}  →  {label}")
            else:
                btn.setText(f"←  {button_name(b)}:  {label}")
            font = btn.font()
            font.setBold(not isinstance(a, DefaultAction))
            btn.setFont(font)
