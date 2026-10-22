import pytest

from naga_config.config.model import Settings
from naga_config.gui.scroll_tab import SYNAPSE_PRESETS, ScrollTab


@pytest.fixture
def tab(qapp):
    t = ScrollTab()
    t.changes = []
    t.changed.connect(lambda: t.changes.append(1))
    return t


def test_six_stages_with_names(tab):
    tab.load(Settings("default"))
    assert [r.title.text() for r in tab.rows] == [f"Stufe {n}" for n in range(1, 7)]
    assert [r.name_edit.text() for r in tab.rows] == ["", "", "", "", "", "Ultra-fine"]
    assert tab.changes == []


def test_active_stage_highlight_and_big_name(tab):
    tab.load(Settings("default"))
    tab.set_status({"scroll_stage": 6})
    assert [r.property("active") for r in tab.rows] == [False] * 5 + [True]
    assert tab.active_name.text() == "Ultra-fine"
    assert "6" in tab.active_slot.text()
    tab.set_status({"scroll_stage": 2})
    assert tab.rows[1].property("active") and not tab.rows[5].property("active")
    assert tab.active_name.text() == "Stufe 2"  # no name given yet


def test_unknown_stage(tab):
    tab.load(Settings("default"))
    for status in ({"scroll_stage": -1}, {}, {"scroll_stage": 9}):
        tab.set_status(status)
        assert tab.active_name.text() == "unbekannt"
        assert not any(r.property("active") for r in tab.rows)


def test_editing_names_updates_settings(tab):
    s = Settings("default")
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
    assert "oberen Taste vorne" in tab.hint.text() and "Razer Synapse" in tab.hint.text()
    assert [p[0] for p in SYNAPSE_PRESETS] == ["Ultra-fine", "Standard", "Distinct",
                                                "Adaptive", "Smooth Scroll", "Custom"]
    t = tab.reference
    assert t.rowCount() == 6 and t.columnCount() == 3
    assert [t.item(0, c).text() for c in range(3)] == ["Ultra-fine", "65", "96"]
    assert [t.item(4, c).text() for c in range(3)] == ["Smooth Scroll", "0", "32"]
    assert t.editTriggers() == t.EditTrigger.NoEditTriggers
