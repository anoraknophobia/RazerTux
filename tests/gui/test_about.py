"""App name, icon and the About dialog (Hilfe → Über RazerTux)."""
import pytest
from fake_daemon import FakeTransport
from PySide6.QtWidgets import QLabel

from razertux import PROJECT_URL, __version__
from razertux.config.model import Profile, Settings, default_profile
from razertux.config.store import ConfigStore
from razertux.gui.about import AboutDialog
from razertux.gui.dbus_client import DaemonClient
from razertux.gui.main_window import MainWindow
from razertux.gui.tray import TrayIcon, app_icon
from razertux.i18n.qt_translator import JsonTranslator


@pytest.fixture
def window(qapp, tmp_path):
    store = ConfigStore(tmp_path)
    store.save_profile(default_profile())
    store.save_profile(Profile(id="p2", name="Zwei"))
    store.save_settings(Settings(active_profile="p2"))
    w = MainWindow(store, DaemonClient(FakeTransport()))
    yield w
    w.close()


@pytest.fixture
def lang(qapp, request):
    t = JsonTranslator(request.param)
    qapp.installTranslator(t)
    yield request.param
    qapp.removeTranslator(t)


def _text(dialog):
    return "\n".join(label.text() for label in dialog.findChildren(QLabel))


def test_window_title_and_tray_tooltip(window):
    assert window.windowTitle() == "RazerTux – Zwei"
    tray = TrayIcon(window.client)
    assert tray.toolTip() == "RazerTux"


def test_app_icon_is_the_razertux_svg(qapp):
    icon = app_icon()
    assert not icon.isNull()
    for size in (16, 22, 32, 64):
        img = icon.pixmap(size, size).toImage()
        assert img.width() == size
        assert any(img.pixelColor(x, y).alpha() > 0 for x in range(size) for y in range(size))


def test_help_menu_opens_about(window, monkeypatch):
    menus = {a.text(): a.menu() for a in window.menuBar().actions()}
    assert list(menus) == ["Ansicht", "Hilfe"]
    assert [a.text() for a in menus["Hilfe"].actions()] == ["Über RazerTux"]
    shown = []
    monkeypatch.setattr(AboutDialog, "exec", lambda self: shown.append(_text(self)) or 0)
    menus["Hilfe"].actions()[0].trigger()
    assert len(shown) == 1 and __version__ in shown[0]


def test_about_dialog_german(qapp):
    d = AboutDialog()
    text = _text(d)
    assert d.windowTitle() == "Über RazerTux"
    for part in ("RazerTux", f"Version {__version__}",
                 "Kein offizielles Razer-Produkt, nicht mit Razer Inc. verbunden.",
                 "GPL-2.0-or-later", "Protokollwissen aus OpenRazer (GPL-2.0)", PROJECT_URL):
        assert part in text, part
    assert not d.windowIcon().isNull()
    assert d.close_button.text() == "Schließen"


@pytest.mark.parametrize("lang, expected", [
    ("en", ("About RazerTux", "Close", "Not an official Razer product; not affiliated with Razer Inc.",
            "Protocol knowledge from OpenRazer (GPL-2.0)")),
    ("es", ("Acerca de RazerTux", "Cerrar", "Razer Inc.", "OpenRazer (GPL-2.0)")),
    ("fr", ("À propos de RazerTux", "Fermer", "Razer Inc.", "OpenRazer (GPL-2.0)")),
], indirect=["lang"])
def test_about_dialog_translated(lang, expected):
    d = AboutDialog()
    text = _text(d)
    assert d.windowTitle() == expected[0]
    assert d.close_button.text() == expected[1]
    for part in expected[2:]:
        assert part in text
    assert "Kein offizielles" not in text and "Protokollwissen" not in text
    assert "GPL-2.0-or-later" in text and __version__ in text
