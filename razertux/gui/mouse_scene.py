"""The mouse drawings (side and top view) as one picture: SVG assets with theme
colours, the positions of the keys on screen and their green highlight."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtSvg import QSvgRenderer

from razertux.gui.laser import laserize
from razertux.gui.layouts import View

ASSETS = Path(__file__).parent / "assets"
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


def laser_svg(name: str, palette: QPalette, hot: str | None = None) -> bytes:
    """A line-art asset with the glow; the line colour is the palette highlight."""
    text = (ASSETS / name).read_text(encoding="utf-8")
    return laserize(text, palette.color(QPalette.ColorRole.Highlight).name(), hot).encode("utf-8")


class Drawing:
    """One SVG asset (of a layout view) placed into a rectangle of a widget."""

    def __init__(self, view: View, ids: list[str]):
        self.view, self.asset, self.ids = view, view.asset, ids
        self._laser = view.style == "laser"
        # filled assets mark their base layer with @BASE@ (redrawn keys sit above the leader lines)
        self._keys_layer = not self._laser and "@BASE@" in (ASSETS / self.asset).read_text(encoding="utf-8")
        self.rect = QRectF()
        # one (renderer, keys-only renderer) per hover state; dropped on a palette change
        self._cache: dict[str | None, tuple[QSvgRenderer, QSvgRenderer | None]] = {}
        self._palette_key: int | None = None
        self.renderer, self.keys = self._build(QPalette(), None)  # replaced by load()
        self._loaded: tuple | None = None

    def _build(self, palette: QPalette, hot: str | None) -> tuple[QSvgRenderer, QSvgRenderer | None]:
        if self._laser:
            return QSvgRenderer(QByteArray(laser_svg(self.asset, palette, hot))), None
        full = QSvgRenderer(QByteArray(themed_svg(self.asset, palette, hot)))
        keys = (QSvgRenderer(QByteArray(themed_svg(self.asset, palette, hot, keys_only=True)))
                if self._keys_layer else None)
        return full, keys

    def load(self, palette: QPalette, hot: str | None) -> None:
        key = (palette.cacheKey(), hot if hot in self.ids else None)
        if key == self._loaded:
            return
        if key[0] != self._palette_key:
            self._cache.clear()
            self._palette_key = key[0]
        if key[1] not in self._cache:
            self._cache[key[1]] = self._build(palette, key[1])
        self.renderer, self.keys = self._cache[key[1]]
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
        rects = {e: self.element_rect(e) for e in self.ids}
        for element in sorted(rects, key=lambda e: rects[e].width() * rects[e].height()):  # small ones first
            if rects[element].contains(pos):
                return element
        return None

    def paint(self, p: QPainter) -> None:
        self.renderer.render(p, self.rect)

    def paint_keys(self, p: QPainter) -> None:
        """Only the keys again (so leader lines run below them)."""
        if self.keys is not None:
            self.keys.render(p, self.rect)
