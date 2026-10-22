import pytest
from fake_daemon import FakeTransport

from naga_config.gui.dbus_client import DaemonClient
from naga_config.gui.tray import TrayIcon


@pytest.fixture
def tray(qapp):
    t = FakeTransport()
    c = DaemonClient(t)
    c.status()
    tr = TrayIcon(c)
    tr.infos = []
    tr._info = lambda title, text: tr.infos.append((title, text))
    tr.confirm = True
    tr._confirm = lambda title, text: tr.confirm
    return tr, t


def test_menu_when_up(tray):
    tr, t = tray
    tr.rebuild_menu()
    texts = tr.menu_texts()
    assert "Aktives Profil: Zwei" in texts and "Standard" in texts
    assert "Akku: 80 %" in texts
    assert texts[-3:] == ["Fenster öffnen", "Originalzustand wiederherstellen", "Beenden"]
    radios = {a.text(): a.isChecked() for a in tr.menu.actions() if a.isCheckable()}
    assert radios == {"Standard": False, "Zwei": True}


def test_menu_when_daemon_down(tray):
    tr, t = tray
    t.up = False
    tr.client.status()
    tr.rebuild_menu()
    assert tr.menu_texts()[0] == "Dienst läuft nicht"


def test_profile_click_activates(tray):
    tr, t = tray
    tr.rebuild_menu()
    next(a for a in tr.menu.actions() if a.text() == "Standard").trigger()
    assert ("ActivateProfile", ("default",)) in t.calls


def test_restore_asks_first_and_reports_failed_fields(tray):
    tr, t = tray
    tr.confirm = False
    tr.restore_original()
    assert not any(c[0] == "RestoreOriginal" for c in t.calls)
    tr.confirm = True
    tr.restore_original()
    assert any(c[0] == "RestoreOriginal" for c in t.calls)
    assert "DPI-Stufen" in tr.infos[-1][1]
