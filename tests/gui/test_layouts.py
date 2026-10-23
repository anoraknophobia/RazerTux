"""Task 9: device layouts (JSON) and their lookup by model."""
import json

import pytest
from PySide6.QtCore import QByteArray, QLineF
from PySide6.QtGui import QPalette
from PySide6.QtSvg import QSvgRenderer

from razertux.devices.db import load_devices
from razertux.gui import layouts, mouse_scene
from razertux.gui.layouts import LAYOUTS, layout_for, parse
from razertux.gui.mouse_view import MouseView
from razertux.gui.theme import dark_palette
from razertux.hw.buttons import ButtonId

FILES = sorted(p for p in LAYOUTS.glob("*.json") if p.name != "index.json")


def renderer(asset, style, palette):
    if style == "laser":
        return QSvgRenderer(QByteArray(mouse_scene.laser_svg(asset, palette)))
    return QSvgRenderer(QByteArray(mouse_scene.themed_svg(asset, palette)))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_every_layout_file_is_consistent(qapp, path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert {ButtonId(k) for k in data["buttons"]}  # all keys are valid ButtonIds
    for v in data["views"]:
        for asset in [v["asset"], *v.get("variants", {}).values()]:
            r = renderer(asset, v.get("style", "filled"), dark_palette())
            assert r.isValid(), asset
            for spec in data["buttons"].values():
                if spec["view"] == v["name"]:
                    assert r.elementExists(spec["element"]), (asset, spec["element"])
    for spec in data["buttons"].values():
        assert spec["side"] in ("left", "right")
        assert spec["view"] in {v["name"] for v in data["views"]}


def test_every_database_model_resolves_to_a_valid_layout(qapp):
    for info in load_devices().values():
        lay = layout_for(info, None)
        assert lay.buttons and lay.views, info.model
        for v in lay.views:
            r = renderer(v.asset, v.style, QPalette())
            assert r.isValid(), (info.model, v.asset)
            for b, spec in lay.buttons.items():
                if spec.view == v.name:
                    assert r.elementExists(spec.element), (info.model, b)


def test_naga_v2_pro_keeps_its_filled_assets():
    lay = layout_for(load_devices()["naga_v2_pro"], None)
    assert lay.template == "naga_v2_pro"
    assert [(v.name, v.asset, v.style) for v in lay.views] == [
        ("side", "naga_v2_pro_side.svg", "filled"), ("top", "naga_v2_pro_top.svg", "filled")]
    assert [b.value for b in lay.buttons_on("left")] == [f"side_{n}" for n in range(1, 13)]
    assert lay.button_for("top", "wheel") == ButtonId.MIDDLE  # the primary one


def test_unknown_model_falls_back_to_generic():
    info = load_devices()["abyssus"]
    assert layout_for(info, None).template == "generic"


def test_prefix_rules_pick_a_template(monkeypatch):
    monkeypatch.setattr(layouts, "_index", lambda: {"aby": "naga_v2_pro", "abyssus_e": "missing"})
    info = load_devices()["abyssus"]
    assert layout_for(info, None).template == "naga_v2_pro"
    # the longest matching rule wins, but a template without a file falls through to generic
    assert layout_for(load_devices()["abyssus_essential"], None).template == "generic"


def test_plate_variants_swap_the_asset():
    data = {"template": "t", "views": [{"name": "plate", "asset": "a.svg",
                                         "variants": {"12": "b.svg", "6": "c.svg"}}],
            "buttons": {"side_1": {"view": "plate", "element": "side_1", "side": "left"}}}
    assert parse(data, 6).views[0].asset == "c.svg"
    assert parse(data, 12).views[0].asset == "b.svg"
    assert parse(data, None).views[0].asset == "a.svg"
    assert parse(data, 2).views[0].asset == "a.svg"


def test_generic_layout_shows_in_the_laser_look(qapp):
    view = MouseView(layout_for(load_devices()["abyssus"], None))
    view.set_bindings({}, {})
    view.resize(900, 560)
    view.show()
    qapp.processEvents()
    for b in view.device_layout.buttons:
        key = view.key_rect(b)
        assert not key.isEmpty() and key.contains(view.leader_line(b)[-1]), b
    view.set_hot(ButtonId.LEFT)
    assert view.drawings["top"]._loaded[1] == "left"
    view.close()


def _hits(rect, seg):
    if rect.contains(seg.p1()) or rect.contains(seg.p2()):
        return True
    edges = [(rect.topLeft(), rect.topRight()), (rect.topRight(), rect.bottomRight()),
             (rect.bottomRight(), rect.bottomLeft()), (rect.bottomLeft(), rect.topLeft())]
    return any(seg.intersects(QLineF(a, b))[0] == QLineF.IntersectionType.BoundedIntersection
               for a, b in edges)


@pytest.mark.parametrize("size", [(1000, 560), (1300, 700)])
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_leader_lines_do_not_cross_other_keys(qapp, path, size):
    view = MouseView(parse(json.loads(path.read_text(encoding="utf-8"))))
    view.set_bindings({}, {})
    view.resize(*size)
    view.show()
    qapp.processEvents()
    for b in view.device_layout.buttons:
        own = view.key_rect(b)
        assert own.contains(view.leader_line(b)[-1]), b
        pts = view.leader_line(b)
        for other in view.device_layout.buttons:
            rect = view.key_rect(other)
            # a key sharing the target's element, or overlapping it (a button's box around
            # the wheel), is no obstacle
            # filled views redraw their keys above the lines (lines run behind them by design)
            hidden = view.device_layout.view(view.device_layout.buttons[other].view).style == "filled"
            if hidden or view.element_of(other) == view.element_of(b) or rect.intersects(own):
                continue
            for a, c in zip(pts, pts[1:]):
                assert not _hits(rect, QLineF(a, c)), (path.stem, b, other)
    view.close()


def test_left_labels_on_a_top_view_enter_outside_the_keys_and_mirror_the_right(qapp):
    view = MouseView(layout_for(load_devices()["abyssus"], None))
    view.set_bindings({}, {})
    view.resize(1000, 560)
    view.show()
    qapp.processEvents()
    lay = view.device_layout
    rect = view.drawings["top"].rect
    for b in lay.buttons_on("left"):
        start, elbow, entry, end = view.leader_line(b)
        assert start.x() < elbow.x() < rect.left() < entry.x() < end.x()
        assert all(entry.x() < view.key_rect(k).left() for k in lay.buttons_on("left"))
    for b in lay.buttons_on("right"):
        start, elbow, entry, end = view.leader_line(b)
        assert start.x() > elbow.x() > rect.right() > entry.x() > end.x()
    view.close()
