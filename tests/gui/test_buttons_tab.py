"""Task 10: the TASTEN tab's side-plate choice (Naga V2 Pro)."""
import pytest

from razertux.config import ConfigStore, DefaultAction, Settings, default_profile
from razertux.config.model import NAGA_MODEL
from razertux.gui.buttons_tab import ButtonsTab
from razertux.hw.buttons import ButtonId


@pytest.fixture
def store(tmp_path):
    return ConfigStore(tmp_path)


@pytest.fixture
def tab(qapp, store):
    t = ButtonsTab(store.for_model(NAGA_MODEL))
    t.resize(1100, 800)
    t.show()
    qapp.processEvents()
    yield t
    t.close()


def test_combo_offers_the_three_plates(tab):
    assert [tab.plate_combo.itemText(i) for i in range(3)] == [
        "Seitenplatte: 12 Tasten", "Seitenplatte: 6 Tasten", "Seitenplatte: 2 Tasten"]
    assert tab.plate_combo.currentData() == 12
    assert tab.plate_hint.isHidden()


@pytest.mark.parametrize("plate,index", [(6, 1), (2, 2)])
def test_choosing_a_plate_saves_and_swaps_the_layout(tab, store, plate, index):
    tab.plate_combo.setCurrentIndex(index)
    assert store.for_model(NAGA_MODEL).load_device_settings().plate == plate
    view = tab.mouse_view
    assert set(view.device_layout.buttons_on("left")) == {ButtonId(f"side_{n}") for n in range(1, plate + 1)}
    assert view.device_layout.views[0].asset == f"naga_v2_pro_plate{plate}.svg"
    assert not tab.plate_hint.isHidden()
    assert tab.plate_hint.text() == "Belegung ungeprüft – falls eine Taste nicht stimmt: Taste anlernen"
    tab.plate_combo.setCurrentIndex(0)
    assert tab.plate_hint.isHidden()
    assert len(tab.mouse_view.device_layout.buttons_on("left")) == 12


def test_saved_plate_is_restored(qapp, store):
    ms = store.for_model(NAGA_MODEL)
    ds = ms.load_device_settings()
    ds.plate = 6
    ms.save_device_settings(ds)
    t = ButtonsTab(ms)
    assert t.plate_combo.currentData() == 6
    assert len(t.mouse_view.device_layout.buttons_on("left")) == 6


def test_plate_change_hides_labels_but_keeps_the_profile(qapp, store):
    from razertux.config.model import Action  # noqa: F401
    ms = store.for_model(NAGA_MODEL)
    t = ButtonsTab(ms)
    profile = default_profile()
    before = profile.to_dict()
    t.mouse_view.set_bindings({}, {})
    t.plate_combo.setCurrentIndex(2)
    assert ButtonId.SIDE_9 not in t.mouse_view._buttons
    assert profile.to_dict() == before
    t.plate_combo.setCurrentIndex(0)
    assert ButtonId.SIDE_9 in t.mouse_view._buttons


def test_bindings_survive_a_plate_change(qapp, store):
    from razertux.config.model import KeyAction
    t = ButtonsTab(store.for_model(NAGA_MODEL))
    t.mouse_view.set_bindings({ButtonId.SIDE_1: KeyAction(30)}, {})
    shown = t.mouse_view.button_text(ButtonId.SIDE_1)
    t.plate_combo.setCurrentIndex(1)
    assert t.mouse_view.button_text(ButtonId.SIDE_1) == shown
