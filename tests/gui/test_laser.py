"""Task 9: laser glow for line-art SVGs."""
import re

import pytest
from PySide6.QtCore import QByteArray
from PySide6.QtSvg import QSvgRenderer

from razertux.gui.laser import laserize

SHAPES = {
    "path": '<path d="M0 0 L10 10" stroke="@ACC@" stroke-width="2"/>',
    "rect": '<rect x="1" y="1" width="8" height="8" stroke="@ACC@" stroke-width="2"/>',
    "circle": '<circle cx="5" cy="5" r="4" stroke="@ACC@" stroke-width="2"/>',
    "ellipse": '<ellipse cx="5" cy="5" rx="4" ry="3" stroke="@ACC@" stroke-width="2"/>',
    "line": '<line x1="0" y1="0" x2="9" y2="9" stroke="@ACC@" stroke-width="2"/>',
}


def doc(body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">{body}</svg>'


@pytest.mark.parametrize("tag", SHAPES)
def test_every_shape_gets_four_glow_strokes_and_a_core(tag):
    out = laserize(doc(SHAPES[tag]), "#44d62c", None)
    assert len(re.findall(rf"<{tag}\b", out)) == 5
    assert re.findall(r'stroke-width="([\d.]+)"', out) == ["14", "8", "4.4", "2", "0.8"]
    assert re.findall(r'stroke-opacity="([\d.]+)"', out) == ["0.06", "0.12", "0.28", "1", "0.5"]
    assert out.count('stroke="#44d62c"') == 4 and out.count('stroke="#d8ffd0"') == 1
    assert "@ACC@" not in out and "fill-opacity" not in out


def test_hot_element_gets_exactly_one_fill():
    body = f'<g id="a">{SHAPES["rect"]}<text x="1" y="1">1</text></g><g id="b">{SHAPES["rect"]}</g>'
    out = laserize(doc(body), "#44d62c", "b")
    assert out.count('fill-opacity="0.35"') == 1
    assert out.count('fill="#44d62c"') == 1
    assert out.index('fill-opacity="0.35"') > out.index('id="b"')
    assert "fill-opacity" not in laserize(doc(body), "#44d62c", None)
    assert "<text" in out  # key labels survive


@pytest.mark.parametrize("hot", [None, "a"])
def test_result_is_a_valid_svg_without_filters(qapp, hot):
    body = "".join(f'<g id="{t[0]}">{s}</g>' for t, s in SHAPES.items())
    out = laserize(doc(body), "#3daee9", hot)
    assert QSvgRenderer(QByteArray(out.encode())).isValid()
    assert "filter" not in out and "<image" not in out
