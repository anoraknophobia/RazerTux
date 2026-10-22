import pytest
from PySide6.QtGui import QPalette

from naga_config.config.model import Settings
from naga_config.config.store import ConfigStore
from naga_config.gui import theme


@pytest.fixture
def restore_style(qapp):
    yield qapp
    theme.apply_theme(qapp, "system")


def test_save_theme_keeps_other_settings(tmp_path):
    store = ConfigStore(tmp_path)
    store.save_settings(Settings("p2", 20, idle_time=300))
    theme.save_theme(store, "system")
    s = store.load_settings()
    assert (s.theme, s.active_profile, s.low_battery_notify, s.idle_time) == ("system", "p2", 20, 300)


def test_apply_synapse_and_back_to_system(restore_style):
    app = restore_style
    theme.apply_theme(app, "system")
    window_before = app.palette().color(QPalette.ColorRole.Window).name()
    theme.apply_theme(app, "synapse")
    assert "#cardTitle" in app.styleSheet() and theme.ACCENT.lower() in app.styleSheet().lower()
    assert app.palette().color(QPalette.ColorRole.Window).name() == theme.WINDOW
    assert app.palette().color(QPalette.ColorRole.Highlight).name() == theme.ACCENT.lower()
    theme.apply_theme(app, "system")
    assert app.styleSheet() == ""
    assert app.palette().color(QPalette.ColorRole.Window).name() == window_before


def test_controller_persists_applies_and_signals(restore_style, tmp_path):
    store = ConfigStore(tmp_path)
    store.save_settings(Settings("default"))
    c = theme.ThemeController(restore_style, store)
    assert c.name == "synapse"
    got = []
    c.changed.connect(got.append)
    c.apply()
    c.set_theme("system")
    assert got == ["system"] and store.load_settings().theme == "system"
    assert restore_style.styleSheet() == ""
    c.set_theme("system")  # unchanged: no signal
    assert got == ["system"]
    c.set_theme("synapse")
    assert restore_style.styleSheet() != "" and store.load_settings().theme == "synapse"
