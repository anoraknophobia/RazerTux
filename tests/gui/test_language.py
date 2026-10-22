import sys

import pytest
from fake_daemon import FakeTransport
from PySide6.QtCore import QCoreApplication

from naga_config.config.model import Profile, Settings, default_profile
from naga_config.config.store import ConfigStore
from naga_config.gui import language
from naga_config.gui.dbus_client import DaemonClient
from naga_config.gui.main_window import MainWindow
from naga_config.gui.tray import TrayIcon
from naga_config.gui.widgets import Card
from naga_config.i18n.qt_translator import JsonTranslator

NAMES = ["Systemsprache", "Deutsch", "English", "Español", "Français"]


@pytest.fixture
def store(tmp_path):
    s = ConfigStore(tmp_path)
    s.save_profile(default_profile())
    s.save_profile(Profile(id="p2", name="Zwei"))
    s.save_settings(Settings(active_profile="p2", theme="system", low_battery_notify=20))
    return s


@pytest.fixture
def english(qapp):
    t = JsonTranslator("en")
    assert qapp.installTranslator(t)
    yield t
    qapp.removeTranslator(t)


def _window(store):
    return MainWindow(store, DaemonClient(FakeTransport()))


def test_translator_answers_known_and_falls_back(qapp, english):
    assert QCoreApplication.translate("MainWindow", "Tasten") == "Customize"
    assert QCoreApplication.translate("MainWindow", "gibt es nicht") == "gibt es nicht"
    assert not english.isEmpty() and JsonTranslator("de").isEmpty()


def test_main_window_in_english(store, english):
    w = _window(store)
    try:
        assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == [
            "Customize", "Performance", "Scroll Wheel", "Lighting", "Power", "Macros"]
        titles = [c.title.text() for c in w.performance_tab.findChildren(Card)]
        assert "SENSITIVITY" in titles and "POLLING RATE" in titles
        assert w.menuBar().actions()[0].text() == "View"
    finally:
        w.close()


def test_language_submenu_in_window(store):
    w = _window(store)
    try:
        assert w.language_menu.title() == "Sprache"
        assert [a.text() for a in w.language_menu.actions()] == NAMES
        w.set_language_checked("en")
        assert [a.isChecked() for a in w.language_menu.actions()] == [False, False, True, False, False]
        got = []
        w.languageRequested.connect(got.append)
        w.language_menu.actions()[4].trigger()
        assert got == ["fr"]
    finally:
        w.close()


def test_language_submenu_in_tray(qapp):
    tray = TrayIcon(DaemonClient(FakeTransport()))
    tray.set_language_checked("es")
    sub = next(a.menu() for a in tray.menu.actions() if a.menu() is not None)
    assert sub.title() == "Sprache"
    assert [a.text() for a in sub.actions()] == NAMES
    assert [a.isChecked() for a in sub.actions()] == [False, False, False, True, False]
    got = []
    tray.languageRequested.connect(got.append)
    sub.actions()[0].trigger()
    assert got == ["system"]


def test_controller_saves_and_offers_restart(store):
    c = language.LanguageController(store)
    assert c.name == "system"
    asked, changed, restarts = [], [], []
    c._ask_restart = lambda: asked.append(1) or True
    c.changed.connect(changed.append)
    c.restartRequested.connect(lambda: restarts.append(1))
    c.set_language("es")
    s = store.load_settings()
    assert (s.language, s.theme, s.low_battery_notify, s.active_profile) == ("es", "system", 20, "p2")
    assert changed == ["es"] and asked == [1] and restarts == [1]
    c._ask_restart = lambda: False
    c.set_language("es")  # unchanged: nothing happens
    c.set_language("de")
    assert changed == ["es", "de"] and restarts == [1]


def test_restart_dialog_texts(store, monkeypatch):
    c = language.LanguageController(store)
    seen = {}

    def fake_exec(box):
        seen["text"] = box.text()
        seen["buttons"] = [b.text() for b in box.buttons()]
        box.buttons()[0].click()  # "Jetzt neu starten"
        return 0
    monkeypatch.setattr(language.QMessageBox, "exec", fake_exec)
    assert c._ask_restart() is True
    assert seen["text"] == "Die Sprache wird nach einem Neustart übernommen."
    assert seen["buttons"] == ["Jetzt neu starten", "Später"]


def test_restart_arguments_keep_options_and_window_state():
    args = ["--tray", "--config-dir", "/x"]
    assert language.restart_args(args, window_visible=True) == ["--config-dir", "/x"]
    assert language.restart_args(["--config-dir", "/x"], window_visible=False) == [
        "--config-dir", "/x", "--tray"]
    assert language.restart_args(args, window_visible=False) == args
    exe, argv = language.restart_command(["--tray"])
    assert exe == sys.executable and argv == ["-m", "naga_config.gui.app", "--tray"]


def test_install_language_sets_both_layers(qapp, store):
    from naga_config import i18n
    s = store.load_settings()
    s.language = "fr"
    store.save_settings(s)
    t = language.install_language(qapp, store)
    try:
        assert i18n.current_language() == "fr"
        assert QCoreApplication.translate("MainWindow", "Tasten") != "Tasten"
    finally:
        qapp.removeTranslator(t)
