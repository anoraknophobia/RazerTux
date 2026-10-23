import pytest

from razertux.config.model import Settings
from razertux.gui.scroll_tab import SYNAPSE_PRESETS, ScrollTab


@pytest.fixture
def tab(qapp):
    t = ScrollTab()
    t.changes = []
    t.changed.connect(lambda: t.changes.append(1))
    return t


def test_six_stages_with_names(tab):
    tab.load(Settings("default"))
    assert [r.title.text() for r in tab.rows] == [f"Stufe {n}" for n in range(1, 7)]
    assert [r.name_edit.text() for r in tab.rows] == ["Ultra-fine", "Standard", "Distinct",
                                                     "Adaptive", "Smooth Scroll", "Custom"]
    assert tab.changes == []


def test_active_stage_highlight_and_big_name(tab):
    tab.load(Settings("default"))
    tab.set_status({"scroll_stage": 6})
    assert [r.property("active") for r in tab.rows] == [False] * 5 + [True]
    assert tab.active_name.text() == "Custom"
    assert "6" in tab.active_slot.text()
    tab.set_status({"scroll_stage": 2})
    assert tab.rows[1].property("active") and not tab.rows[5].property("active")
    assert tab.active_name.text() == "Standard"
    tab.load(Settings("default", scroll_stage_names={}))
    tab.set_status({"scroll_stage": 2})
    assert tab.active_name.text() == "Stufe 2"  # no name given


def test_unknown_stage(tab):
    tab.load(Settings("default"))
    for status in ({"scroll_stage": -1}, {}, {"scroll_stage": 9}):
        tab.set_status(status)
        assert tab.active_name.text() == "unbekannt"
        assert not any(r.property("active") for r in tab.rows)


def test_editing_names_updates_settings(tab):
    s = Settings("default", scroll_stage_names={6: "Ultra-fine"})
    tab.load(s)
    tab.set_status({"scroll_stage": 3})
    tab.rows[2].name_edit.setText("Distinct")
    tab.rows[2].name_edit.textEdited.emit("Distinct")
    assert s.scroll_stage_names == {3: "Distinct", 6: "Ultra-fine"}
    assert tab.active_name.text() == "Distinct" and tab.changes == [1]
    tab.rows[5].name_edit.setText("  ")
    tab.rows[5].name_edit.textEdited.emit("  ")
    assert s.scroll_stage_names == {3: "Distinct"}  # empty name: removed


def test_hint_and_reference(tab):
    names = tab.findChildren(type(tab.hint))
    texts = " ".join(w.text() for w in names)
    assert "nicht bekannt" not in texts and "außer Stufe 6" not in texts
    assert "Reihenfolge" in texts and "umbenennen" in texts
    assert "oberen Taste vorne" in tab.hint.text() and "Razer Synapse" in tab.hint.text()
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
