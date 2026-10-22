"""The mouse drawings (side and top view) as one picture: SVG assets with theme
colours, the positions of the keys on screen and their green highlight."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).parent / "assets"
SIDE_SVG = "naga_v2_pro_side.svg"
TOP_SVG = "naga_v2_pro_top.svg"
SIDE_IDS = tuple(f"side_{n}" for n in range(1, 13))
TOP_IDS = ("wheel", "top_1", "top_2", "left", "right")  # small ones first (hit test)
HOT_OPACITY = "0.55"
_HOT = re.compile(r"@HOT:(\w+)@")

# dark (Synapse) look of the mouse body: fixed, like the real (black) mouse
_DARK = {"@BODY_A@": "#1a1a1a", "@BODY_B@": "#2b2b2b", "@BODY_C@": "#353535",
         "@BODY_D@": "#101010", "@EDGE@": "#3d3d3d", "@KEY_A@": "#2a2a2a",
         "@KEY_B@": "#141414", "@KEY_TXT@": "#e8e8e8"}


def svg_colours(palette: QPalette) -> dict[str, str]:
    """Token -> colour. Dark palettes get the black mouse, light ones a body
    derived from the palette; the accent is always the palette highlight."""
    role = QPalette.ColorRole
    colours = {
        "@FG@": palette.color(role.WindowText).name(),
        "@BG@": palette.color(role.Base).name(),
        "@MUTE@": palette.color(role.Mid).name(),
        "@ACC@": palette.color(role.Highlight).name(),
    }
    if palette.color(role.Window).lightness() < 110:
        colours.update(_DARK)
    else:
        mid, button = palette.color(role.Mid), palette.color(role.Button)
        colours.update({
            "@BODY_A@": mid.darker(160).name(), "@BODY_B@": mid.darker(115).name(),
            "@BODY_C@": mid.name(), "@BODY_D@": mid.darker(220).name(),
            "@EDGE@": palette.color(role.Dark).name(), "@KEY_A@": button.name(),
            "@KEY_B@": button.darker(115).name(),
            "@KEY_TXT@": palette.color(role.ButtonText).name(),
        })
    return colours


def themed_svg(name: str, palette: QPalette, hot: str | None = None,
               keys_only: bool = False) -> bytes:
    """Load an asset, fill its @TOKENS@ with theme colours and light up `hot`.
    keys_only hides everything marked @BASE@ (the layer above the leader lines)."""
    text = (ASSETS / name).read_text(encoding="utf-8")
    text = text.replace("@BASE@", "0" if keys_only else "1")
    for token, value in svg_colours(palette).items():
        text = text.replace(token, value)
    text = _HOT.sub(lambda m: HOT_OPACITY if m.group(1) == hot else "0", text)
    return text.encode("utf-8")


class Drawing:
    """One SVG asset placed into a rectangle of a widget."""

    def __init__(self, asset: str, ids: tuple[str, ...], keys_layer: bool = False):
        self.asset, self.ids = asset, ids
        self.renderer = QSvgRenderer()
        self.keys = QSvgRenderer() if keys_layer else None
        self.rect = QRectF()
        self._loaded: tuple | None = None

    def load(self, palette: QPalette, hot: str | None) -> None:
        key = (palette.cacheKey(), hot if hot in self.ids else None)
        if key != self._loaded:
            self.renderer.load(QByteArray(themed_svg(self.asset, palette, key[1])))
            if self.keys is not None:
                self.keys.load(QByteArray(themed_svg(self.asset, palette, key[1], keys_only=True)))
            self._loaded = key

    def aspect(self) -> float:
        box = self.renderer.viewBoxF()
        return box.width() / box.height() if box.height() else 1.0

    def place(self, rect: QRectF) -> None:
        """Fit the drawing (keeping its aspect) centred into `rect`."""
        a = self.aspect()
        w = min(rect.width(), rect.height() * a)
        h = w / a
        self.rect = QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)

    def element_rect(self, element: str) -> QRectF:
        """Bounding box of an element in widget coordinates."""
        box = self.renderer.viewBoxF()
        if box.isEmpty() or self.rect.isEmpty():
            return QRectF()
        local = self.renderer.transformForElement(element).mapRect(
            self.renderer.boundsOnElement(element))
        sx, sy = self.rect.width() / box.width(), self.rect.height() / box.height()
        return QRectF(self.rect.left() + (local.left() - box.left()) * sx,
                      self.rect.top() + (local.top() - box.top()) * sy,
                      local.width() * sx, local.height() * sy)

    def element_at(self, pos: QPointF) -> str | None:
        for element in self.ids:
            if self.element_rect(element).contains(pos):
                return element
        return None

    def paint(self, p: QPainter) -> None:
        self.renderer.render(p, self.rect)

    def paint_keys(self, p: QPainter) -> None:
        """Only the keys again (so leader lines run below them)."""
        if self.keys is not None:
            self.keys.render(p, self.rect)
