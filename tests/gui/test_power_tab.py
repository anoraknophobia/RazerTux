import pytest

from naga_config.config.model import Settings
from naga_config.gui.power_tab import PowerTab


@pytest.fixture
def tab(qapp):
    t = PowerTab()
    t.changes = []
    t.changed.connect(lambda: t.changes.append(1))
    return t


def test_defaults_leave_mouse_untouched(tab):
    s = Settings("default")
    tab.load(s)
    assert not tab.idle_box.isChecked() and not tab.low_power_box.isChecked()
    assert tab.warn_box.isChecked() and tab.warn_spin.value() == 15
    assert (s.idle_time, s.low_battery_threshold, s.low_battery_notify) == (None, None, 15)
    assert tab.changes == []


def test_idle_minutes_to_seconds(tab):
    s = Settings("default")
    tab.load(s)
    tab.idle_box.setChecked(True)
    assert s.idle_time == 300
    tab.idle_slider.setValue(15)
    assert s.idle_time == 900 and "15" in tab.idle_value.text()
    tab.idle_slider.setValue(1)
    assert s.idle_time == 60
    tab.idle_box.setChecked(False)
    assert s.idle_time is None


def test_low_power_threshold(tab):
    s = Settings("default", low_battery_threshold=10)
    tab.load(s)
    assert tab.low_power_box.isChecked() and tab.low_power_slider.value() == 10
    assert (tab.low_power_slider.minimum(), tab.low_power_slider.maximum()) == (5, 25)
    tab.low_power_slider.setValue(25)
    assert s.low_battery_threshold == 25


def test_warning_level(tab):
    s = Settings("default", low_battery_notify=0)
    tab.load(s)
    assert not tab.warn_box.isChecked()
    tab.warn_box.setChecked(True)
    assert s.low_battery_notify == 15
    tab.warn_spin.setValue(30)
    assert s.low_battery_notify == 30
    tab.warn_box.setChecked(False)
    assert s.low_battery_notify == 0
