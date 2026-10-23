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
    plates = data.get("plate_buttons", {})
    for v in data["views"]:
        for asset in [v["asset"], *v.get("variants", {}).values()]:
            r = renderer(asset, v.get("style", "filled"), dark_palette())
            assert r.isValid(), asset
            for spec in data["buttons"].values():
                if spec["view"] == v["name"]:
                    assert r.elementExists(spec["element"]), (asset, spec["element"])
        for plate, asset in v.get("variants", {}).items():
            r = renderer(asset, v.get("style", "filled"), dark_palette())
            for spec in plates.get(plate, {}).values():
                if spec["view"] == v["name"]:
                    assert r.elementExists(spec["element"]), (asset, spec["element"])
    for spec in [*data["buttons"].values(), *(s for p in plates.values() for s in p.values())]:
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


NAGA = load_devices()["naga_v2_pro"]
PLATE_KEYS = {12: 12, 6: 6, 2: 2}


@pytest.mark.parametrize("plate", [12, 6, 2])
def test_naga_v2_pro_laser_views_per_plate(plate):
    lay = layout_for(NAGA, plate)
    assert lay.template == "naga_v2_pro"
    assert [(v.name, v.asset, v.style) for v in lay.views] == [
        ("plate", f"naga_v2_pro_plate{plate}.svg", "laser"), ("top", "naga_v2_pro_top.svg", "laser")]
    assert {b.value for b in lay.buttons_on("left")} == {f"side_{n}" for n in range(1, plate + 1)}
    assert all(lay.buttons[ButtonId(f"side_{n}")].element == f"side_{n}" for n in range(1, plate + 1))
    assert lay.elements("top") == ["left", "right", "wheel", "top_1", "top_2"]
    assert lay.button_for("top", "wheel") == ButtonId.MIDDLE  # the primary one


def test_naga_v2_pro_old_filled_assets_are_gone():
    assets = LAYOUTS.parent
    assert not (assets / "naga_v2_pro_side.svg").exists()


def test_six_key_plate_is_printed_6_5_4_left_and_1_2_3_right(qapp):
    view = MouseView(layout_for(NAGA, 6))
    view.resize(1100, 800)
    view.show()
    qapp.processEvents()
    x = {n: view.key_rect(ButtonId(f"side_{n}")).center() for n in range(1, 7)}
    assert x[6].x() < x[1].x() and x[5].x() < x[2].x() and x[4].x() < x[3].x()
    assert x[6].y() < x[5].y() < x[4].y() and x[1].y() < x[2].y() < x[3].y()
    view.close()


def test_buttons_outside_the_plate_have_no_label(qapp):
    view = MouseView(layout_for(NAGA, 2))
    assert set(view.device_layout.buttons_on("left")) == {ButtonId.SIDE_1, ButtonId.SIDE_2}
    assert ButtonId.SIDE_3 not in view._buttons


def test_plate_buttons_are_merged_per_plate():
    data = {"template": "t", "views": [{"name": "plate", "asset": "a.svg", "variants": {"12": "b.svg", "2": "c.svg"}}],
            "plate_buttons": {"12": {"side_1": {"view": "plate", "element": "side_1", "side": "left"},
                                     "side_2": {"view": "plate", "element": "side_2", "side": "left",
                                                "entry": 0.1, "via": [[0.2, 0.3]]}},
                              "2": {"side_1": {"view": "plate", "element": "side_1", "side": "left"}}},
            "buttons": {"left": {"view": "top", "element": "left", "side": "right", "through": ["right"]}}}
    assert list(parse(data, 2).buttons) == [ButtonId.SIDE_1, ButtonId.LEFT]
    lay = parse(data, 12)
    assert lay.buttons[ButtonId.SIDE_2].entry == 0.1 and lay.buttons[ButtonId.SIDE_2].via == ((0.2, 0.3),)
    assert lay.buttons[ButtonId.LEFT].through == ("right",)
    assert ButtonId.SIDE_2 in parse(data, None).buttons  # no plate given: the first one


def test_unknown_model_falls_back_to_generic():
    info = load_devices()["hyper_polling_wireless"]  # a dongle, not a mouse
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
    view = MouseView(layout_for(load_devices()["hyper_polling_wireless"], None))
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


@pytest.mark.parametrize("plate", [12, 6, 2])
@pytest.mark.parametrize("size", [(1000, 560), (1300, 700), (1100, 800)])
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_leader_lines_do_not_cross_other_keys(qapp, path, size, plate):
    view = MouseView(parse(json.loads(path.read_text(encoding="utf-8")), plate))
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
            if (hidden or view.element_of(other) == view.element_of(b) or rect.intersects(own)
                    or view.element_of(other) in view.device_layout.buttons[b].through):
                continue
            for a, c in zip(pts, pts[1:]):
                assert not _hits(rect, QLineF(a, c)), (path.stem, b, other)
    view.close()


def test_left_labels_on_a_top_view_enter_outside_the_keys_and_mirror_the_right(qapp):
    view = MouseView(layout_for(load_devices()["hyper_polling_wireless"], None))
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


# -- Task 11: templates, generic fallback with learn dots, per-model layouts --------------

WHEEL3 = {"wheel_up", "middle", "wheel_down"}
TILT = {"wheel_left", "wheel_right"}
THUMB = {"forward", "back"}
BASE = {"left", "right"} | WHEEL3
EXPECTED = {  # slug -> (template, view names, button set)
    "death_adder_v2": ("template_ergo", BASE | THUMB | {"dpi_up", "dpi_down"}),
    "death_adder_v3": ("template_ergo", BASE | THUMB),
    "death_adder_v3_pro": ("template_ergo", BASE | THUMB),
    "viper": ("template_ambi", BASE | THUMB),
    "viper_v2_pro": ("template_ambi", BASE | THUMB),
    "viper_mini": ("template_ambi", BASE | THUMB),
    "basilisk_v3": ("template_ergo", BASE | TILT | THUMB),
    "basilisk_v3_pro": ("template_ergo", BASE | TILT | THUMB),
    "cobra": ("template_ergo", BASE | THUMB),
    "cobra_pro": ("template_ergo", BASE | THUMB),
    "orochi_v2": ("template_compact", BASE | THUMB),
}


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_mouse_models_have_their_own_layout_file(slug):
    template, buttons = EXPECTED[slug]
    assert (LAYOUTS / f"{slug}.json").is_file()
    lay = layout_for(load_devices()[slug], None)
    assert lay.template == template
    assert {b.value for b in lay.buttons} == buttons
    assert [v.style for v in lay.views] == ["laser"]
    # thumb keys sit on the left flank, clicks and wheel on the right
    assert {b.value for b in lay.buttons_on("left")} >= THUMB


@pytest.mark.parametrize("slug,plates", [("naga_pro", (12, 6, 2)), ("naga_trinity", (12, 7, 2))])
def test_swappable_plate_nagas_have_plate_variants(slug, plates):
    for plate in plates:
        lay = layout_for(load_devices()[slug], plate)
        assert lay.template == slug
        assert [v.name for v in lay.views] == ["plate", "top"]
        assert lay.view("plate").asset == f"template_mmo_plate{plate}.svg"
        assert {b.value for b in lay.buttons_on("left")} == {f"side_{n}" for n in range(1, plate + 1)}
        assert {"left", "right", "middle"} <= {b.value for b in lay.buttons_on("right")}


@pytest.mark.parametrize("slug", ["naga_x", "naga_v2_hyper_speed"])
def test_fixed_twelve_key_nagas(slug):
    lay = layout_for(load_devices()[slug], None)
    assert {b.value for b in lay.buttons_on("left")} == {f"side_{n}" for n in range(1, 13)}


def test_prefix_rules_map_the_families_to_templates():
    rules = json.loads((LAYOUTS / "index.json").read_text(encoding="utf-8"))["prefixes"]
    devices = load_devices()
    want = {"death_adder_1800": "template_ergo", "basilisk_x_hyper_speed": "template_ergo",
            "cobra_hyper_speed": "template_ergo", "pro_click_v2": "template_ergo",
            "viper_ultimate": "template_ambi", "lancehead_te": "template_ambi", "mamba_te": "template_ambi",
            "taipan": "template_ambi", "orochi_2013": "template_compact", "atheris": "template_compact",
            "pro_click_mini": "template_compact", "pro_click_v2_vertical_edition": "template_vertical",
            "naga_epic": "template_mmo", "naga_2014": "template_mmo",
            "hyper_polling_wireless": "generic", "naga_left_handed_2020": "generic"}
    for slug, template in want.items():
        assert layout_for(devices[slug], None).template == template, slug
    assert all((LAYOUTS / f"{t}.json").is_file() for t in rules.values())


def test_generic_fallback_shows_numbered_learn_dots_on_the_flank():
    lay = layout_for(load_devices()["hyper_polling_wireless"], None)
    learn = [b for b in lay.buttons if b.value.startswith("learn_")]
    assert [b.value for b in learn] == [f"learn_{n}" for n in range(1, 9)]
    assert all(lay.buttons[b].side == "left" for b in learn)
    assert {"left", "right", "middle"} <= {b.value for b in lay.buttons}


def test_the_vertical_template_is_a_side_view_with_thumb_keys():
    lay = layout_for(load_devices()["pro_click_v2_vertical_edition"], None)
    assert lay.views[0].asset == "template_vertical_side.svg"
    assert {"left", "right", "middle", "forward", "back"} <= {b.value for b in lay.buttons}


def test_all_laser_assets_follow_the_line_art_format():
    import xml.etree.ElementTree as ET
    from razertux.gui.laser import SHAPES
    for path in sorted(LAYOUTS.parent.glob("*.svg")):
        if path.name == "razertux.svg":
            continue
        text = path.read_text(encoding="utf-8")
        assert "<image" not in text and "<filter" not in text and "style=" not in text, path.name
        for el in ET.fromstring(text).iter():
            tag = el.tag.rpartition("}")[2]
            if tag in SHAPES:
                assert el.get("stroke") and "fill" not in el.attrib, (path.name, tag)
