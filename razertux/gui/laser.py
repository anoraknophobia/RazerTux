"""Laser look for line-art SVGs: every line shape becomes four glow strokes plus a
bright core (the same recipe as the approved mock-up). QtSvg has no SVG filters, so
the glow is made of wider, fainter copies of the stroke.

Asset format: only path/rect/circle/ellipse/line with ``stroke`` and no ``fill``;
keys are ``<g id="<element>">`` with exactly one shape (plus an optional ``<text>``);
``@ACC@`` stands for the line colour; no filters, no images."""
from __future__ import annotations

import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

SHAPES = frozenset({"path", "rect", "circle", "ellipse", "line"})
GLOW = ((7.0, "0.06"), (4.0, "0.12"), (2.2, "0.28"), (1.0, "1"))  # width factor, opacity
CORE = "#d8ffd0"
CORE_FACTOR, CORE_OPACITY = 0.4, "0.5"
HOT_FILL_OPACITY = "0.35"
_DROP = ("fill", "fill-opacity", "stroke", "stroke-width", "stroke-opacity", "style")
_SKIP = frozenset({"defs", "clipPath", "mask"})  # not drawn: shapes in here stay as they are


def _tag(el: ET.Element) -> str:
    return el.tag.rpartition("}")[2]


def _stroke(shape: ET.Element, colour: str, width: float, opacity: str) -> ET.Element:
    attrs = {k: v for k, v in shape.attrib.items() if k not in _DROP}
    attrs.update({"fill": "none", "stroke": colour, "stroke-opacity": opacity,
                  "stroke-width": f"{width:g}", "stroke-linejoin": "round",
                  "stroke-linecap": "round"})
    return ET.Element(shape.tag, attrs)


def _expand(shape: ET.Element, accent: str, hot: bool) -> list[ET.Element]:
    colour = shape.get("stroke") or accent
    base = float(shape.get("stroke-width", "1"))
    out = []
    if hot:
        fill = ET.Element(shape.tag, {k: v for k, v in shape.attrib.items() if k not in _DROP})
        fill.set("fill", accent)
        fill.set("fill-opacity", HOT_FILL_OPACITY)
        fill.set("stroke", "none")
        out.append(fill)
    out += [_stroke(shape, colour, base * f, op) for f, op in GLOW]
    out.append(_stroke(shape, CORE, base * CORE_FACTOR, CORE_OPACITY))
    return out


def _walk(parent: ET.Element, accent: str, hot: str | None, in_hot: bool) -> None:
    children = []
    for child in list(parent):
        if _tag(child) in _SKIP:
            children.append(child)
            continue
        if _tag(child) in SHAPES:
            children += _expand(child, accent, in_hot)
            continue
        _walk(child, accent, hot, in_hot or (hot is not None and child.get("id") == hot))
        children.append(child)
    parent[:] = children


def laserize(svg: str, accent: str, hot: str | None) -> str:
    """Glow version of a line-art SVG; `hot` (a key's group id) also gets a fill."""
    root = ET.fromstring(svg.replace("@ACC@", accent))
    _walk(root, accent, hot, False)
    return ET.tostring(root, encoding="unicode")
