import pytest

from razertux.config.model import Settings
from razertux.gui.scroll_tab import SYNAPSE_PRESETS, ScrollTab


@pytest.fixture
def tab(qapp):
    t = ScrollTab()
    t.changes = []
    t.changed.connect(lambda: t.changes.append(1))
    return t


def test_six_stages_with_fixed_names(tab):
    from PySide6.QtWidgets import QLineEdit
    tab.load(Settings("default", scroll_stage_names={1: "Fein"}))  # old user names are ignored
    assert [r.title.text() for r in tab.rows] == [f"Stage {n}" for n in range(1, 7)]
    assert [r.name.text() for r in tab.rows] == ["Ultra-fine", "Standard", "Distinct",
                                                "Adaptive", "Smooth Scroll", "Custom"]
    assert tab.findChildren(QLineEdit) == []  # names cannot be edited
    assert tab.changes == []


def test_custom_stage_is_marked_work_in_progress(tab):
    tab.load(Settings("default"))
    assert [not r.wip.isHidden() for r in tab.rows] == [False] * 5 + [True]
    assert "WIP" in tab.rows[5].wip.text() and "Synapse" in tab.rows[5].wip.text()


def test_click_on_a_stage_requests_it(tab, qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    tab.load(Settings("default"))
    asked = []
    tab.stageRequested.connect(asked.append)
    tab.show()
    for slot in (3, 1, 6):
        QTest.mouseClick(tab.rows[slot - 1], Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert asked == [3, 1, 6]  # "Custom" too: the mouse uses its stored values
    assert all(r.selectable for r in tab.rows)
    tab.hide()


def test_active_stage_highlight_and_big_name(tab):
    tab.load(Settings("default", scroll_stage_names={2: "Fein"}))
    tab.set_status({"scroll_stage": 6})
    assert [r.property("active") for r in tab.rows] == [False] * 5 + [True]
    assert tab.active_name.text() == "Custom"
    assert "6" in tab.active_slot.text()
    tab.set_status({"scroll_stage": 2})
    assert tab.rows[1].property("active") and not tab.rows[5].property("active")
    assert tab.active_name.text() == "Standard"
    assert tab.tension_value.text() == "60" and tab.steps_value.text() == "24"


def test_unknown_stage(tab):
    tab.load(Settings("default"))
    for status in ({"scroll_stage": -1}, {}, {"scroll_stage": 9}):
        tab.set_status(status)
        assert tab.active_name.text() == "unknown"
        assert not any(r.property("active") for r in tab.rows)


def test_hint_and_reference(tab):
    names = tab.findChildren(type(tab.hint))
    texts = " ".join(w.text() for w in names)
    assert "rename" not in texts and "Click a stage" in texts
    assert "front top button" in tab.hint.text() and "Razer Synapse" in tab.hint.text()
    assert [p[0] for p in SYNAPSE_PRESETS] == ["Ultra-fine", "Standard", "Distinct",
                                                "Adaptive", "Smooth Scroll", "Custom"]
    t = tab.reference
    assert t.rowCount() == 6 and t.columnCount() == 3
    assert [t.item(0, c).text() for c in range(3)] == ["Ultra-fine", "65", "96"]
    assert [t.item(4, c).text() for c in range(3)] == ["Smooth Scroll", "0", "32"]
    assert t.editTriggers() == t.EditTrigger.NoEditTriggers


def _info(model):
    from razertux.devices.db import load_devices
    return load_devices()[model]


def test_basilisk_shows_scroll_options_but_no_naga_cards(tab):
    tab.set_device(_info("basilisk_v3"))
    assert not tab.options_card.isHidden() and tab.naga_cards.isHidden()
    assert set(tab.mode_buttons) == {None, 0, 1}


def test_naga_keeps_its_cards_without_options(tab):
    tab.set_device(_info("naga_v2_pro"))
    assert tab.options_card.isHidden() and not tab.naga_cards.isHidden()


def test_options_follow_the_device_features(tab):
    from dataclasses import replace
    info = _info("basilisk_v3")
    tab.set_device(replace(info, features=info.features - {"scroll_smart_reel", "scroll_acceleration"}))
    assert not tab.mode_row.isHidden()
    assert tab.accel_row.isHidden() and tab.reel_row.isHidden()


def test_profile_options_roundtrip(tab):
    from razertux.config.model import Profile
    tab.set_device(_info("basilisk_v3"))
    p = Profile(id="p", name="P")
    tab.load_profile(p)
    assert tab.mode_buttons[None].isChecked() and tab.changes == []
    tab.mode_buttons[1].click()
    tab.accel_buttons[False].click()
    tab.reel_buttons[True].click()
    assert p.scroll == {"mode": 1, "acceleration": False, "smart_reel": True}
    assert len(tab.changes) == 3
    tab.reel_buttons[None].click()
    assert p.scroll == {"mode": 1, "acceleration": False}
    tab.load_profile(Profile(id="q", name="Q", scroll={"mode": 0, "acceleration": True}))
    assert tab.mode_buttons[0].isChecked() and tab.accel_buttons[True].isChecked()
    assert tab.reel_buttons[None].isChecked()
