"""Task 14: mouse drawings, leader lines, hover/click mapping, header, footer language."""
import re

import pytest
from fake_daemon import FakeTransport
from PySide6.QtCore import QByteArray, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFocusEvent, QMouseEvent, QPalette
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from naga_config.config.model import Settings, default_profile
from naga_config.config.store import ConfigStore
from naga_config.gui import language, mouse_scene
from naga_config.gui.dbus_client import DaemonClient
from naga_config.gui.main_window import MainWindow
from naga_config.gui.mouse_view import OTHER_BUTTONS, SIDE_BUTTONS, MouseView, element_of
from naga_config.gui.theme import dark_palette
from naga_config.hw.buttons import ButtonId

ALL = [*SIDE_BUTTONS, *OTHER_BUTTONS]


# -- SVG assets ---------------------------------------------------------------------
@pytest.mark.parametrize("asset, ids", [(mouse_scene.SIDE_SVG, mouse_scene.SIDE_IDS),
                                        (mouse_scene.TOP_SVG, mouse_scene.TOP_IDS)])
def test_svgs_are_valid_and_have_the_key_elements(qapp, asset, ids):
    r = QSvgRenderer(QByteArray(mouse_scene.themed_svg(asset, dark_palette())))
    assert r.isValid()
    for element in ids:
        assert r.elementExists(element), element
        assert not r.boundsOnElement(element).isEmpty()
    filled = re.sub(r"<!--.*?-->", "", mouse_scene.themed_svg(asset, dark_palette()).decode(), flags=re.S)
    assert "@" not in filled  # every token filled


def test_side_keys_are_numbered_1_to_12():
    text = (mouse_scene.ASSETS / mouse_scene.SIDE_SVG).read_text(encoding="utf-8")
    groups = re.findall(r'<g id="side_(\d+)">(.*?)</g>', text, re.S)
    assert [int(n) for n, _ in groups] == list(range(1, 13))
    for n, body in groups:
        assert re.search(rf">{n}</text>", body), n


def test_svg_colours_dark_and_system(qapp):
    dark = mouse_scene.svg_colours(dark_palette())
    assert (dark["@BODY_A@"], dark["@BODY_B@"], dark["@ACC@"]) == ("#1a1a1a", "#2b2b2b", "#44d62c")
    light = QPalette(QColor("#efefef"))
    light.setColor(QPalette.ColorRole.Highlight, QColor("#3daee9"))
    sys_colours = mouse_scene.svg_colours(light)
    assert sys_colours["@ACC@"] == "#3daee9"
    assert sys_colours["@BODY_A@"] != "#1a1a1a"  # derived from the palette


def test_hot_and_keys_only_tokens(qapp):
    hot = mouse_scene.themed_svg(mouse_scene.SIDE_SVG, dark_palette(), hot="side_3").decode()
    assert hot.count(f'fill-opacity="{mouse_scene.HOT_OPACITY}"') == 1
    keys = mouse_scene.themed_svg(mouse_scene.SIDE_SVG, dark_palette(), keys_only=True).decode()
    assert '<g opacity="0">' in keys and '<g opacity="0">' not in mouse_scene.themed_svg(
        mouse_scene.SIDE_SVG, dark_palette()).decode()


# -- view geometry ------------------------------------------------------------------
@pytest.fixture
def view(qapp):
    v = MouseView()
    v.set_bindings({}, {}, top_locked=False)
    v.resize(1000, 560)
    v.show()
    qapp.processEvents()
    yield v
    v.close()


@pytest.mark.parametrize("size", [(1000, 560), (1300, 700), (800, 420)])
def test_leader_lines_end_on_their_key_after_resize(view, qapp, size):
    view.resize(*size)
    qapp.processEvents()
    for b in ALL:
        pts = view.leader_line(b)
        start, end = pts[0], pts[-1]
        key, label = view.key_rect(b), view.label_rect(b)
        assert not key.isEmpty()
        assert key.contains(end), b
        assert label.top() <= start.y() <= label.bottom(), b
        gap = start.x() - label.right() if b in SIDE_BUTTONS else label.left() - start.x()
        assert 0 < gap <= 12, b
        assert view.rect().contains(label.toRect()), b  # labels stay inside the view


def test_labels_and_pictures_do_not_overlap(view):
    pictures = view.side.rect.united(view.top.rect)
    for b in ALL:
        assert not view.label_rect(b).intersects(pictures), b


def test_elements_of_buttons():
    assert element_of(ButtonId.SIDE_7) == "side_7"
    assert {element_of(b) for b in (ButtonId.MIDDLE, ButtonId.WHEEL_UP, ButtonId.WHEEL_LEFT)} == {"wheel"}
    assert element_of(ButtonId.TOP_2) == "top_2" and element_of(ButtonId.LEFT) == "left"


# -- hover / click ------------------------------------------------------------------
def _mouse(view, kind, pos):
    p = QPointF(pos)
    e = QMouseEvent(kind, p, view.mapToGlobal(p), Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseButtonRelease
                    else Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view, e)


def _centre(view, b):
    """A point on the key that belongs to no other key (centre of the bare key)."""
    return view.key_rect(b).center()


def test_hover_on_label_lights_key(view):
    btn = view._buttons[ButtonId.SIDE_5]
    QApplication.sendEvent(btn, QEvent(QEvent.Type.Enter))
    assert view.hot == ButtonId.SIDE_5
    assert view.side._loaded[1] == "side_5"  # the drawing shows the key green
    assert btn.property("hot") is True
    QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
    assert view.hot is None and btn.property("hot") is False


@pytest.mark.parametrize("b", [ButtonId.SIDE_1, ButtonId.SIDE_6, ButtonId.SIDE_12,
                               ButtonId.LEFT, ButtonId.RIGHT, ButtonId.TOP_1, ButtonId.TOP_2])
def test_hover_and_click_on_key_maps_to_its_label(view, b):
    pos = _centre(view, b)
    assert view.button_at(pos) == b
    _mouse(view, QEvent.Type.MouseMove, pos)
    assert view.hot == b and view._buttons[b].property("hot") is True
    got = []
    view.bindingClicked.connect(got.append)
    _mouse(view, QEvent.Type.MouseButtonPress, pos)
    _mouse(view, QEvent.Type.MouseButtonRelease, pos)
    assert got == [b]


def test_click_on_wheel_opens_scroll_click(view):
    assert view.button_at(_centre(view, ButtonId.WHEEL_UP)) == ButtonId.MIDDLE


def test_locked_top_buttons_neither_hover_nor_click(view):
    view.set_bindings({}, {}, top_locked=True)
    pos = _centre(view, ButtonId.TOP_1)
    assert view.button_at(pos) is None
    got = []
    view.bindingClicked.connect(got.append)
    _mouse(view, QEvent.Type.MouseButtonRelease, pos)
    QApplication.sendEvent(view._buttons[ButtonId.TOP_1], QEvent(QEvent.Type.Enter))
    assert got == [] and view.hot is None


def test_click_outside_keys_does_nothing(view):
    got = []
    view.bindingClicked.connect(got.append)
    _mouse(view, QEvent.Type.MouseButtonRelease, QPoint(2, 2))
    assert got == []


# -- main window: key click, header, footer language ----------------------------------
@pytest.fixture
def store(tmp_path):
    s = ConfigStore(tmp_path)
    s.save_profile(default_profile())
    s.save_settings(Settings(active_profile="default", language="system"))
    return s


@pytest.fixture
def window(qapp, store):
    w = MainWindow(store, DaemonClient(FakeTransport()))
    w.resize(1100, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()


def test_click_on_key_opens_the_action_dialog(window, monkeypatch):
    from naga_config.gui import main_window
    seen = []
    monkeypatch.setattr(main_window.ActionDialog, "pick",
                        staticmethod(lambda parent, action, macros, button, layer: seen.append(button)))
    view = window.mouse_view
    pos = view.key_rect(ButtonId.SIDE_4).center()
    _mouse(view, QEvent.Type.MouseButtonPress, pos)
    _mouse(view, QEvent.Type.MouseButtonRelease, pos)
    view._buttons[ButtonId.SIDE_4].click()
    assert seen == [ButtonId.SIDE_4, ButtonId.SIDE_4]


def test_customize_page_fits_1100x800_without_scrolling(window):
    view = window.mouse_view
    assert view.height() >= view.minimumHeight() and view.width() >= view.minimumWidth()
    for b in ALL:
        assert view.rect().contains(view.label_rect(b).toRect())
    pill = window.buttons_tab.layer_pill
    assert pill.isVisible() and abs(pill.geometry().center().x()
                                    - window.buttons_tab.width() / 2) <= 2  # centred


def test_layer_pill_switches_layer(window):
    window.buttons_tab.layer_buttons["hypershift"].click()
    assert window.layer == "hypershift"
    assert window.buttons_tab.layer_buttons["hypershift"].isChecked()
    assert not window.buttons_tab.layer_buttons["standard"].isChecked()


def test_header_title_and_battery_glyph(window):
    assert window.header.title.text() == "Naga V2 Pro"
    centre = window.header.title.geometry().center().x()
    assert abs(centre - window.header.width() / 2) <= 2
    window.client.statusChanged.emit({"connected": True, "battery": 80, "charging": False,
                                      "connection": "wireless", "dpi": 1600})
    bat = window.battery_label
    assert (bat.text(), bat.level, bat.charging) == ("80 %", 80, False)
    assert "⚡" not in bat.text() and bat.sizeHint().width() > bat.fontMetrics().horizontalAdvance("80 %")
    assert window.dpi_label.text() == "1600 DPI"


def test_profile_actions_in_the_dots_menu(window):
    texts = [a.text() for a in window.header.menu_button.menu().actions()]
    assert texts == ["Neu", "Umbenennen", "Duplizieren", "Löschen", "Export …", "Import …"]


def test_footer_language_combo_syncs_with_menu_and_offers_restart(window, store):
    combo = window.language_combo
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Systemsprache", "Deutsch", "English", "Español", "Français"]
    assert combo.currentData() == "system"
    # wired like app.py
    controller = language.LanguageController(store)
    asked = []
    controller._ask_restart = lambda: asked.append(1) or False
    window.languageRequested.connect(controller.set_language)
    controller.changed.connect(window.set_language_checked)
    combo.activated.emit(combo.findData("fr"))
    assert controller.name == "fr" and store.load_settings().language == "fr"
    assert asked == [1]
    checked = [a.data() for a in window.language_menu.actions() if a.isChecked()]
    assert checked == ["fr"] and combo.currentData() == "fr"
    window.language_menu.actions()[2].trigger()  # English from the menu
    assert combo.currentData() == "en" and asked == [1, 1]


def test_footer_language_combo_is_bottom_left(window):
    combo = window.language_combo
    pos = combo.mapTo(window, QPoint(0, 0))
    assert pos.x() <= 24 and pos.y() > window.height() - 70
    assert pos.x() < window.revert_btn.mapTo(window, QPoint(0, 0)).x()


# -- review fixes: line geometry, ids, focus, press/release ---------------------------
def _segments(pts):
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)
            if (pts[i] - pts[i + 1]).manhattanLength() > 1e-6]


def _cross(a, b):
    def orient(p, q, r):
        v = (q.x() - p.x()) * (r.y() - p.y()) - (q.y() - p.y()) * (r.x() - p.x())
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    (p1, p2), (p3, p4) = a, b
    o1, o2, o3, o4 = orient(p1, p2, p3), orient(p1, p2, p4), orient(p3, p4, p1), orient(p3, p4, p2)
    if 0 not in (o1, o2, o3, o4):
        return o1 != o2 and o3 != o4
    return min(_dist(p3, a), _dist(p4, a), _dist(p1, b), _dist(p2, b)) < 1e-6  # touching


def _dist(p, seg):
    a, b = seg
    dx, dy = b.x() - a.x(), b.y() - a.y()
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy)
                                                 / (dx * dx + dy * dy)))
    return ((p.x() - a.x() - t * dx) ** 2 + (p.y() - a.y() - t * dy) ** 2) ** 0.5


def _rect_hit(rect, seg):
    a, b = seg
    if rect.contains(a) or rect.contains(b):
        return True
    corners = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
    return any(_cross(seg, (corners[i], corners[(i + 1) % 4])) for i in range(4))


@pytest.mark.parametrize("size", [(982, 796), (1100, 800), (1600, 1000)])
def test_leader_lines_never_cross_and_keep_clear_of_dots(window, qapp, size):
    window.resize(*size)
    qapp.processEvents()
    view = window.mouse_view
    lines = {b: view.leader_line(b) for b in ALL}
    wheel = view.top.element_rect("wheel")
    for b, pts in lines.items():
        for other, opts in lines.items():
            if other == b:
                continue
            for seg in _segments(opts):
                for dot in (pts[0], pts[-1]):
                    assert _dist(dot, seg) >= 6, (size, b, other)
                for mine in _segments(pts):
                    assert not _cross(mine, seg), (size, b, other)
        if element_of(b) != "wheel":
            assert not any(_rect_hit(wheel, seg) for seg in _segments(pts)), (size, b)


@pytest.mark.parametrize("asset", [mouse_scene.SIDE_SVG, mouse_scene.TOP_SVG])
def test_svg_ids_are_unique(asset):
    ids = re.findall(r'\bid="([^"]+)"', (mouse_scene.ASSETS / asset).read_text(encoding="utf-8"))
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


def test_keyboard_focus_on_label_lights_key(view):
    btn = view._buttons[ButtonId.SIDE_9]
    btn.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.sendEvent(btn, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
    assert view.hot == ButtonId.SIDE_9 and btn.property("hot") is True
    QApplication.sendEvent(btn, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
    assert view.hot is None


def test_release_counts_only_when_pressed_on_the_same_key(view):
    got = []
    view.bindingClicked.connect(got.append)
    _mouse(view, QEvent.Type.MouseButtonPress, _centre(view, ButtonId.SIDE_1))
    _mouse(view, QEvent.Type.MouseButtonRelease, _centre(view, ButtonId.SIDE_2))
    assert got == []
    _mouse(view, QEvent.Type.MouseButtonPress, QPoint(2, 2))
    _mouse(view, QEvent.Type.MouseButtonRelease, _centre(view, ButtonId.SIDE_2))
    assert got == []
    _mouse(view, QEvent.Type.MouseButtonPress, _centre(view, ButtonId.SIDE_2))
    _mouse(view, QEvent.Type.MouseButtonRelease, _centre(view, ButtonId.SIDE_2))
    assert got == [ButtonId.SIDE_2]


def test_themed_svgs_are_cached_per_hover_state(view, monkeypatch):
    calls = []
    real = mouse_scene.themed_svg
    monkeypatch.setattr(mouse_scene, "themed_svg", lambda *a, **k: calls.append(a) or real(*a, **k))
    for _ in range(2):
        view.set_hot(ButtonId.SIDE_3)
        view.set_hot(ButtonId.SIDE_4)
    assert len(calls) <= 4  # each state built once (side: full + keys layer), then reused
