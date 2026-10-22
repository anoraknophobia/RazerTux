import pytest
from PySide6.QtGui import QColor

from razertux.config.model import LightingZone, Profile, validate
from razertux.gui.lighting_tab import LightingTab


@pytest.fixture
def tab(qapp):
    t = LightingTab()
    t.changes = []
    t.changed.connect(lambda: t.changes.append(1))
    t.next_color = QColor(255, 0, 0)
    t._pick_color = lambda current: t.next_color
    return t


def test_empty_lighting_not_changed_then_enable(tab):
    p = Profile(id="p", name="P")
    tab.load(p)
    assert not tab.enable_box.isChecked() and p.lighting == {} and tab.changes == []
    tab.enable_box.setChecked(True)
    assert set(p.lighting) == {"logo", "thumb"}  # the Naga V2 Pro has no wheel LED
    assert p.lighting["logo"] == LightingZone("static", (68, 214, 44), 191)
    assert tab.all_same.isChecked() and validate(p) == []
    tab.enable_box.setChecked(False)
    assert p.lighting == {}


def test_brightness_percent_maps_to_255(tab):
    p = Profile(id="p", name="P", lighting={"logo": LightingZone("static", (1, 2, 3), 255),
                                            "thumb": LightingZone("static", (1, 2, 3), 255)})
    tab.load(p)
    assert tab.brightness.value() == 100
    tab.brightness.setValue(60)
    assert p.lighting["logo"].brightness == 153 and p.lighting["thumb"].brightness == 153
    tab.brightness.setValue(0)
    assert p.lighting["logo"].brightness == 0


def test_all_zones_same_effect_and_colour(tab):
    p = Profile(id="p", name="P")
    tab.load(p)
    tab.enable_box.setChecked(True)
    tab.set_effect("all", "breath")
    assert p.lighting["logo"].effect == p.lighting["thumb"].effect == "breath"
    tab.color_buttons["all"].click()
    assert p.lighting["logo"].color == p.lighting["thumb"].color == (255, 0, 0)


def test_separate_zones(tab):
    p = Profile(id="p", name="P", lighting={"logo": LightingZone("spectrum", (0, 0, 0), 100),
                                            "thumb": LightingZone("static", (0, 0, 255), 100)})
    tab.load(p)
    assert not tab.all_same.isChecked()  # differing zones load as separate
    assert not tab.effect_combos["logo"].isHidden() and tab.effect_combos["all"].isHidden()
    tab.set_effect("thumb", "reactive")
    assert p.lighting["thumb"].effect == "reactive" and p.lighting["logo"].effect == "spectrum"
    tab.all_same.setChecked(True)  # logo is copied to every zone
    assert p.lighting["thumb"].effect == "spectrum"


def test_colour_button_only_for_colour_effects(tab):
    p = Profile(id="p", name="P")
    tab.load(p)
    tab.enable_box.setChecked(True)
    for effect, visible in [("static", True), ("breath", True), ("reactive", True),
                            ("spectrum", False), ("wave", False), ("off", False)]:
        tab.set_effect("all", effect)
        assert tab.color_buttons["all"].isHidden() is not visible, effect


def test_cancelled_colour_dialog_changes_nothing(tab):
    p = Profile(id="p", name="P", lighting={"logo": LightingZone("static", (1, 2, 3), 9),
                                            "thumb": LightingZone("static", (1, 2, 3), 9)})
    tab.load(p)
    tab.next_color = None
    tab.color_buttons["all"].click()
    assert p.lighting["logo"].color == (1, 2, 3) and tab.changes == []


def test_scroll_zone_is_kept_but_not_shown(tab):
    scroll = LightingZone("off", (0, 0, 0), 70)
    p = Profile(id="p", name="P", lighting={"logo": LightingZone("static", (1, 2, 3), 9),
                                            "scroll": scroll})
    tab.load(p)
    assert "scroll" not in tab.effect_combos
    tab.set_effect("all", "wave")
    assert p.lighting["scroll"] == scroll and p.lighting["logo"].effect == "wave"
