import pytest

from razertux.config.model import Profile, validate
from razertux.gui.performance_tab import PerformanceTab


@pytest.fixture
def tab(qapp):
    t = PerformanceTab()
    t.changes = []
    t.changed.connect(lambda: t.changes.append(1))
    return t


def staged(n=3, active=2, **kw):
    return Profile(id="p", name="P", dpi_stages=[(800 * (i + 1),) * 2 for i in range(n)],
                   active_stage=active, **kw)


def test_empty_profile_is_not_changed(tab):
    p = Profile(id="p", name="P")
    tab.load(p)
    assert not tab.set_dpi_box.isChecked() and not tab.rows_box.isEnabled()
    assert tab.poll_buttons[None].isChecked()
    assert p.dpi_stages == [] and p.poll_rate is None and tab.changes == []


def test_enable_dpi_writes_single_stage(tab):
    p = Profile(id="p", name="P")
    tab.load(p)
    tab.set_dpi_box.setChecked(True)
    assert p.dpi_stages == [(1800, 1800)] and p.active_stage == 1
    assert not tab.stages_toggle.isChecked() and len(tab.rows) == 1
    assert tab.changes and validate(p) == []
    tab.set_dpi_box.setChecked(False)  # back to "Nicht ändern"
    assert p.dpi_stages == [] and p.active_stage == 0


def test_load_stages_roundtrip_without_edit(tab):
    p = staged()
    tab.load(p)
    assert tab.set_dpi_box.isChecked() and tab.stages_toggle.isChecked()
    assert [r.spin_x.value() for r in tab.rows] == [800, 1600, 2400]
    assert tab.rows[1].radio.isChecked()
    assert p.dpi_stages == [(800, 800), (1600, 1600), (2400, 2400)] and tab.changes == []


def test_sixth_stage_rejected(tab):
    p = staged(n=4, active=1)
    tab.load(p)
    assert tab.add_stage() is True
    assert len(p.dpi_stages) == 5 and not tab.add_btn.isEnabled()
    assert tab.add_stage() is False
    assert len(p.dpi_stages) == 5 and len(tab.rows) == 5


def test_spin_rounds_to_50_and_couples_y(tab):
    p = staged(n=2, active=1)
    tab.load(p)
    row = tab.rows[0]
    row.spin_x.setValue(1234)
    assert row.spin_x.value() == 1250 and row.slider.value() * 50 == 1250
    assert p.dpi_stages[0] == (1250, 1250)
    row.slider.setValue(60)  # 3000
    assert row.spin_x.value() == 3000 and p.dpi_stages[0] == (3000, 3000)


def test_split_xy(tab):
    p = staged(n=2, active=1)
    tab.load(p)
    tab.split_box.setChecked(True)
    assert not tab.rows[0].spin_y.isHidden()
    tab.rows[0].spin_y.setValue(400)
    assert p.dpi_stages[0] == (800, 400)
    tab.load(p)  # differing X/Y loads as split
    assert tab.split_box.isChecked()
    tab.split_box.setChecked(False)  # coupling again: Y follows X
    assert p.dpi_stages[0] == (800, 800)


def test_active_stage_radio_and_remove(tab):
    p = staged()
    tab.load(p)
    tab.rows[2].radio.setChecked(True)
    assert p.active_stage == 3
    tab.remove_stage(2)
    assert p.dpi_stages == [(800, 800), (1600, 1600)] and p.active_stage == 2
    tab.remove_stage(0)
    assert p.dpi_stages == [(1600, 1600)] and p.active_stage == 1
    assert not tab.rows[0].remove_btn.isEnabled()


def test_stages_toggle_off_on_keeps_all_stages(tab):
    p = staged(n=5, active=3)
    tab.load(p)
    tab.stages_toggle.setChecked(False)  # profile gets only the active stage
    assert p.dpi_stages == [(2400, 2400)] and p.active_stage == 1
    assert tab.rows[0].radio.isHidden() and tab.add_btn.isHidden()
    tab.rows[0].spin_x.setValue(2500)  # edited while single
    tab.stages_toggle.setChecked(True)  # all five stages come back
    assert p.dpi_stages == [(800, 800), (1600, 1600), (2500, 2500), (3200, 3200), (4000, 4000)]
    assert p.active_stage == 3 and len(tab.rows) == 5


def test_stages_toggle_on_from_single_adds_second_stage(tab):
    p = staged(n=1, active=1)
    tab.load(p)
    tab.stages_toggle.setChecked(True)
    assert p.dpi_stages == [(800, 800), (1600, 1600)] and p.active_stage == 1


def test_round_half_up(tab):
    from razertux.gui.performance_tab import _round_dpi
    assert (_round_dpi(125), _round_dpi(175), _round_dpi(124)) == (150, 200, 100)


def test_poll_rate_buttons(tab):
    p = Profile(id="p", name="P", poll_rate=500)
    tab.load(p)
    assert tab.poll_buttons[500].isChecked()
    tab.poll_buttons[1000].click()
    assert p.poll_rate == 1000
    tab.poll_buttons[None].click()
    assert p.poll_rate is None
