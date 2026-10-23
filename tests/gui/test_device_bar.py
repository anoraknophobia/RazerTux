"""Task 12: device selector pills and the "Experimentell" banner."""
import pytest
from fake_daemon import DA, NAGA, FakeTransport
from PySide6.QtGui import QGuiApplication

from razertux.devices.db import load_devices
from razertux.gui.dbus_client import DaemonClient
from razertux.gui.device_bar import DeviceBar, tab_keys

DEVICES = [(NAGA, "Razer Naga V2 Pro", "wireless", True), (DA, "Razer DeathAdder V2", "wired", True)]
EXPERIMENTAL = {"connected": True, "experimental": True, "writable": True, "model": DA}


@pytest.fixture
def bar(qapp):
    transport = FakeTransport()
    transport.add_second_device()
    b = DeviceBar(DaemonClient(transport))
    b.transport = transport
    b.got = []
    b.deviceChanged.connect(b.got.append)
    b.show()
    yield b
    b.close()


def test_pills_for_every_device_and_switching(bar):
    bar.set_devices(DEVICES, NAGA)
    assert [bar.pills[m].text() for m in (NAGA, DA)] == ["Naga V2 Pro", "DeathAdder V2"]
    assert bar.pills[NAGA].isChecked()
    bar.pills[DA].click()
    assert bar.got == [DA]
    bar.select(NAGA)
    assert bar.pills[NAGA].isChecked() and bar.got == [DA]  # select() does not emit


def test_disconnected_device_is_marked(bar):
    bar.set_devices([DEVICES[0], (DA, "Razer DeathAdder V2", "wired", False)], NAGA)
    assert "nicht verbunden" in bar.pills[DA].toolTip()


def test_banner_only_for_experimental_devices(bar):
    bar.set_status({"connected": True, "experimental": False, "writable": True}, remap_enabled=True)
    assert not bar.banner.isVisible()
    bar.set_status(EXPERIMENTAL, remap_enabled=False)
    assert bar.banner.isVisible()
    assert bar.banner_title.text() == "Experimentell – ungetestet"
    assert bar.diag_btn.text() == "Diagnose kopieren"
    assert bar.remap_toggle.text() == "Tastenbelegung aktivieren"
    assert not bar.remap_toggle.isChecked() and not bar.readonly_label.isVisible()


def test_read_only_hint(bar):
    bar.set_status({**EXPERIMENTAL, "writable": False}, remap_enabled=True)
    assert bar.readonly_label.isVisible()
    assert bar.readonly_label.text() == (
        "Nur lesend: die Maus hat auf die Prüfbefehle nicht wie erwartet geantwortet.")
    assert bar.remap_toggle.isChecked()


def test_copy_diagnostics_to_clipboard(bar):
    bar.set_devices(DEVICES, DA)
    bar.set_status(EXPERIMENTAL, remap_enabled=False)
    QGuiApplication.clipboard().setText("")
    bar.diag_btn.click()
    assert QGuiApplication.clipboard().text() == "probe ok"
    assert ("GetDiagnostics", (DA,)) in bar.transport.calls


def test_remap_switch_emits(bar):
    got = []
    bar.remapToggled.connect(got.append)
    bar.set_status(EXPERIMENTAL, remap_enabled=False)
    bar.remap_toggle.click()
    assert got == [True]
    bar.set_status(EXPERIMENTAL, remap_enabled=False)  # loading does not emit
    assert got == [True]


def test_tabs_only_for_existing_functions():
    db = load_devices()
    assert tab_keys(db[NAGA]) == ["buttons", "performance", "scroll", "lighting", "power", "macros"]
    assert tab_keys(db[DA]) == ["buttons", "performance", "lighting", "macros"]
    assert "lighting" not in tab_keys(db["death_adder_v3"])  # no zones
    assert "power" in tab_keys(db["basilisk_v3_pro"])         # battery
    assert "performance" in tab_keys(db["abyssus"])           # poll rate only, no DPI
