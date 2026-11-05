"""Every device of the database through the main window: a function a device lacks must
never crash a tab (issue #3: a Naga without lighting zones)."""
import pytest
from fake_daemon import FakeTransport

from razertux.config.model import LightingZone, Profile, Settings
from razertux.config.store import ConfigStore
from razertux.devices.db import load_all_devices
from razertux.gui.dbus_client import DaemonClient
from razertux.gui.main_window import MainWindow

FULL = Profile(id="full", name="Full", dpi_stages=[(400, 400), (800, 800), (1600, 1600)], poll_rate=1000,
               lighting={z: LightingZone("static", (1, 2, 3), 100) for z in ("logo", "thumb", "scroll")})


@pytest.fixture
def window(qapp, tmp_path):
    store = ConfigStore(tmp_path)
    store.save_settings(Settings())
    transport = FakeTransport()
    w = MainWindow(store, DaemonClient(transport))
    w._warn = lambda title, text: None
    w._ask_unsaved = lambda: "discard"
    yield w, store, transport
    w.close()


@pytest.mark.parametrize("model", sorted(load_all_devices()))
def test_every_device_opens_and_loads_a_full_profile(window, model):
    w, store, transport = window
    name = load_all_devices()[model].name
    transport.devices[:] = [["naga_v2_pro", "Razer Naga V2 Pro", "wired", True], [model, name, "wired", True]]
    transport.statuses[model] = {"connected": True, "battery": -1}
    transport.profiles[model] = ["full"]
    transport.active[model] = "full"
    store.for_model(model).save_profile(FULL)
    w.switch_device(model)
    w._show_profile(FULL)
    for i in range(w.tabs.count()):
        w.tabs.setCurrentIndex(i)
    w.save()
    w.switch_device("naga_v2_pro")


def _keyboard_pids():
    return sorted({(m, pid) for m, d in load_all_devices().items() if d.kind == "keyboard"
                   for pid in d.pids})


@pytest.mark.parametrize("model,pid", _keyboard_pids())
def test_every_keyboard_page_opens(qapp, model, pid):
    from razertux.gui.keyboard_view import KeyboardPage
    from razertux.recorder.discover import FoundDevice
    page = KeyboardPage()
    page.set_device(FoundDevice(0x1532, pid, "0200", "", "", []))  # no product string either
    assert page.title.text()
    page.resize(900, 500)
    page.grab()  # paints the picture
