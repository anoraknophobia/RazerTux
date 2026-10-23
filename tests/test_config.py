import json

import pytest
from evdev import ecodes as E

from razertux.config import (
    ComboAction, ConfigStore, DefaultAction, DisabledAction, DpiStageAction,
    HypershiftAction, KeyAction, LightingZone, Macro, MacroAction, Profile,
    Settings, DelayStep, KeyStep, ButtonStep, default_profile, new_profile_id,
    validate,
)
from razertux.hw.buttons import ButtonId


def full_profile():
    p = default_profile()
    p.id, p.name = "gaming", "Gaming"
    p.standard[ButtonId.SIDE_1] = KeyAction(E.KEY_A)
    p.standard[ButtonId.SIDE_2] = ComboAction([E.KEY_LEFTCTRL, E.KEY_C])
    p.standard[ButtonId.SIDE_3] = MacroAction("m1", "repeat_n", 3)
    p.standard[ButtonId.SIDE_4] = DisabledAction()
    p.standard[ButtonId.SIDE_12] = HypershiftAction()
    p.hypershift[ButtonId.SIDE_1] = KeyAction(E.KEY_B)
    p.dpi_stages = [(800, 800), (1600, 1600)]
    p.active_stage = 2
    p.poll_rate = 500
    p.lighting = {"logo": LightingZone("static", (1, 2, 3), 200)}
    p.remap_top_buttons = True
    return p


def test_default_profile_leaves_device_untouched():
    p = default_profile()
    assert (p.id, p.name) == ("default", "Standard")
    assert p.dpi_stages == [] and p.active_stage == 0
    assert p.poll_rate is None and p.lighting == {} and p.remap_top_buttons is False
    assert p.hypershift == {}
    assert p.standard[ButtonId.TOP_1] == DpiStageAction("up")
    assert p.standard[ButtonId.TOP_2] == DpiStageAction("cycle")  # R20
    assert set(p.standard) == set(ButtonId)
    assert p.standard[ButtonId.LEFT] == DefaultAction()
    assert validate(p) == []


def test_profile_roundtrip(tmp_path):
    store = ConfigStore(tmp_path)
    p = full_profile()
    store.save_profile(p)
    assert store.load_profile("gaming") == p
    assert Profile.from_dict(json.loads(json.dumps(p.to_dict()))) == p


def test_profile_file_is_pretty_utf8(tmp_path):
    store = ConfigStore(tmp_path)
    p = default_profile()
    p.name = "Größe"
    store.save_profile(p)
    text = (tmp_path / "profiles" / "naga_v2_pro" / "default.json").read_text(encoding="utf-8")
    assert "Größe" in text and "\n  " in text
    assert not list((tmp_path / "profiles" / "naga_v2_pro").glob("*.tmp"))


def test_left_click_lockout_rejected():
    p = default_profile()
    p.standard[ButtonId.LEFT] = DisabledAction()
    assert "Linksklick nicht erreichbar" in validate(p)


def test_left_click_remapped_elsewhere_ok():
    p = default_profile()
    p.standard[ButtonId.LEFT] = DisabledAction()
    p.standard[ButtonId.SIDE_1] = KeyAction(E.BTN_LEFT)
    assert validate(p) == []


def test_two_hypershift_keys_rejected():
    p = default_profile()
    p.standard[ButtonId.SIDE_1] = HypershiftAction()
    p.standard[ButtonId.SIDE_2] = HypershiftAction()
    assert any("Hypershift" in e for e in validate(p))


def test_missing_macro_reported():
    p = default_profile()
    p.standard[ButtonId.SIDE_1] = MacroAction("weg", "once")
    assert "Makro fehlt: weg" in validate(p, {})
    assert validate(p) == []
    assert validate(p, {"weg": Macro("weg", "W", [])}) == []


@pytest.mark.parametrize("change", [
    {"dpi_stages": [(100, 100)] * 6, "active_stage": 1},
    {"dpi_stages": [(50, 100)], "active_stage": 1},
    {"dpi_stages": [(100, 30001)], "active_stage": 1},
    {"dpi_stages": [(800, 800)], "active_stage": 0},
    {"dpi_stages": [(800, 800)], "active_stage": 2},
    {"poll_rate": 250},
    {"lighting": {"logo": LightingZone("static", (0, 0, 0), 256)}},
    {"lighting": {"logo": LightingZone("static", (0, 256, 0), 10)}},
])
def test_invalid_values_rejected(change):
    p = default_profile()
    for k, v in change.items():
        setattr(p, k, v)
    assert validate(p)


def test_valid_values_accepted():
    assert validate(full_profile(), {"m1": Macro("m1", "M", [])}) == []


def test_corrupt_profile_falls_back(tmp_path):
    (tmp_path / "profiles" / "naga_v2_pro").mkdir(parents=True)
    (tmp_path / "profiles/naga_v2_pro/x.json").write_text("{kaputt")
    s = ConfigStore(tmp_path)
    assert [p.id for p in s.list_profiles()] == ["default"]
    assert s.errors and "x.json" in s.errors[0]
    assert not (tmp_path / "profiles/naga_v2_pro/default.json").exists()


def test_unknown_action_type_is_corrupt(tmp_path):
    s = ConfigStore(tmp_path)
    p = default_profile()
    s.save_profile(p)
    d = p.to_dict()
    d["id"] = "evil"
    d["standard"]["side_1"] = {"type": "zauber"}
    (tmp_path / "profiles/naga_v2_pro/evil.json").write_text(json.dumps(d))
    s2 = ConfigStore(tmp_path)
    assert [x.id for x in s2.list_profiles()] == ["default"]
    assert any("evil.json" in e for e in s2.errors)


def test_corrupt_settings_and_macros(tmp_path):
    (tmp_path / "settings.json").write_text("nope")
    (tmp_path / "macros.json").write_text("[")
    s = ConfigStore(tmp_path)
    assert s.load_settings() == Settings(active_profile="default")
    assert s.load_macros() == {}
    assert any("settings.json" in e for e in s.errors)
    assert any("macros.json" in e for e in s.errors)


def test_settings_and_macros_roundtrip(tmp_path):
    s = ConfigStore(tmp_path)
    assert s.load_settings() == Settings(active_profile="default")
    s.save_settings(Settings("gaming", 20))
    assert s.load_settings() == Settings("gaming", 20)
    m = {"m1": Macro("m1", "Test", [KeyStep(E.KEY_A, True), DelayStep(10),
                                    ButtonStep(E.BTN_LEFT, False)])}
    s.save_macros(m)
    assert s.load_macros() == m
    assert s.errors == []


def test_delete_profile(tmp_path):
    s = ConfigStore(tmp_path)
    s.save_profile(full_profile())
    s.delete_profile("gaming")
    assert [p.id for p in s.list_profiles()] == ["default"]


def test_import_with_existing_id_gets_new_id(tmp_path):
    s = ConfigStore(tmp_path)
    s.save_profile(full_profile())
    out = tmp_path / "exp.json"
    s.export_profile("gaming", out)
    imp = s.import_profile(out)
    assert imp.id != "gaming" and imp.name == "Gaming"
    assert s.load_profile(imp.id) == imp
    assert len(s.list_profiles()) == 2


def test_new_profile_id_is_filesystem_safe():
    a, b = new_profile_id("Mein Süßes Profil!"), new_profile_id("Mein Süßes Profil!")
    assert a != b
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in a)
    assert new_profile_id("§§§")


@pytest.mark.parametrize("button", [ButtonId.TOP_1, ButtonId.TOP_2])
def test_hypershift_on_top_button_needs_remap(button):
    msg = "Hypershift-Taste auf oberer Taste nur mit „Obere Tasten frei belegen“"
    p = default_profile()
    p.standard[button] = HypershiftAction()
    assert msg in validate(p)
    p.remap_top_buttons = True
    assert validate(p) == []


def test_hypershift_key_in_hypershift_layer_rejected():
    p = default_profile()
    p.hypershift[ButtonId.SIDE_1] = HypershiftAction()
    assert "Hypershift-Taste in der Hypershift-Ebene nicht erlaubt" in validate(p)


def test_load_profile_corrupt_returns_default_and_records(tmp_path):
    (tmp_path / "profiles" / "naga_v2_pro").mkdir(parents=True)
    (tmp_path / "profiles/naga_v2_pro/kaputt.json").write_text("{x")
    s = ConfigStore(tmp_path)
    assert s.load_profile("kaputt").id == "default"
    assert any("kaputt.json" in e for e in s.errors)


def test_load_profile_missing_returns_default_and_records(tmp_path):
    s = ConfigStore(tmp_path)
    assert s.load_profile("weg").id == "default"
    assert any("weg.json" in e for e in s.errors)
    s2 = ConfigStore(tmp_path)
    assert s2.load_profile("default").id == "default" and s2.errors == []


def test_export_unreadable_profile_raises_valueerror(tmp_path):
    (tmp_path / "profiles" / "naga_v2_pro").mkdir(parents=True)
    (tmp_path / "profiles/naga_v2_pro/kaputt.json").write_text("{x")
    s = ConfigStore(tmp_path)
    with pytest.raises(ValueError, match="kaputt.json"):
        s.export_profile("kaputt", tmp_path / "o.json")
    with pytest.raises(ValueError, match="weg.json"):
        s.export_profile("weg", tmp_path / "o.json")


@pytest.mark.parametrize("bad", ["../x", "Gaming", "a/b"])
def test_import_unsafe_id_regenerated(tmp_path, bad):
    s = ConfigStore(tmp_path)
    d = full_profile().to_dict()
    d["id"] = bad
    f = tmp_path / "in.json"
    f.write_text(json.dumps(d))
    imp = s.import_profile(f)
    assert imp.id != bad and s.load_profile(imp.id) == imp


def test_settings_new_fields_roundtrip_and_defaults(tmp_path):
    s = ConfigStore(tmp_path)
    full = Settings("gaming", 20, theme="system", idle_time=600, low_battery_threshold=10)
    s.save_settings(full)
    assert s.load_settings() == full
    old = Settings.from_dict({"active_profile": "x", "low_battery_notify": 15})
    assert (old.theme, old.idle_time, old.low_battery_threshold) == ("synapse", None, None)


def test_dpi_cycle_action_roundtrip():
    p = default_profile()
    assert Profile.from_dict(json.loads(json.dumps(p.to_dict()))).standard[ButtonId.TOP_2] \
        == DpiStageAction("cycle")


def test_old_profile_with_scroll_profile_loads_and_is_not_written(tmp_path):
    d = default_profile().to_dict()
    assert "scroll_profile" not in d and d["remap_top_buttons"] is False
    d.pop("remap_top_buttons")
    d["scroll_profile"] = 3  # written by older versions
    store = ConfigStore(tmp_path)
    (tmp_path / "profiles" / "naga_v2_pro").mkdir(parents=True)
    (tmp_path / "profiles" / "naga_v2_pro" / "default.json").write_text(json.dumps(d))
    p = store.load_profile("default")
    assert store.errors == [] and p.remap_top_buttons is False
    assert not hasattr(p, "scroll_profile")
    store.save_profile(p)
    on_disk = json.loads((tmp_path / "profiles" / "naga_v2_pro" / "default.json").read_text())
    assert "scroll_profile" not in on_disk and on_disk["remap_top_buttons"] is False


SYNAPSE_ORDER = {1: "Ultra-fine", 2: "Standard", 3: "Distinct", 4: "Adaptive", 5: "Smooth Scroll", 6: "Custom"}


def test_settings_scroll_stage_names(tmp_path):
    s = Settings("default")
    assert s.scroll_stage_names == SYNAPSE_ORDER  # R25: Synapse list order, user-verified
    assert Settings("x").scroll_stage_names is not s.scroll_stage_names  # no shared default
    s.scroll_stage_names = {1: "Fein", 6: "Ultra-fine"}
    store = ConfigStore(tmp_path)
    store.save_settings(s)
    assert store.load_settings().scroll_stage_names == {1: "Fein", 6: "Ultra-fine"}
    assert Settings.from_dict({"active_profile": "a"}).scroll_stage_names == SYNAPSE_ORDER


def test_old_default_stage_names_are_migrated(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps(
        {"active_profile": "default", "scroll_stage_names": {"6": "Ultra-fine"}}))
    assert ConfigStore(tmp_path).load_settings().scroll_stage_names == SYNAPSE_ORDER
    # anything the user named themselves is kept as is
    for names in ({"6": "Ultra-fine", "1": "Fein"}, {"6": "Fein"}, {}):
        got = Settings.from_dict({"active_profile": "a", "scroll_stage_names": names})
        assert got.scroll_stage_names == {int(k): v for k, v in names.items()}


# --- schema 2: per model ----------------------------------------------------

def test_for_model_isolates_profiles_and_legacy_delegates(tmp_path):
    from razertux.config import ConfigStore, default_profile
    s = ConfigStore(tmp_path)
    p = default_profile()
    p.id, p.name = "a", "A"
    s.save_profile(p)
    assert (tmp_path / "profiles" / "naga_v2_pro" / "a.json").exists()
    assert [x.id for x in s.for_model("naga_v2_pro").list_profiles()] == ["a"]
    other = s.for_model("viper")
    assert [x.id for x in other.list_profiles()] == ["default"]
    other.save_profile(other.default_profile())
    assert (tmp_path / "profiles" / "viper" / "default.json").exists()


def test_settings_active_profile_maps_to_naga():
    from razertux.config import Settings
    s = Settings("x")
    assert s.active_profiles == {"naga_v2_pro": "x"}
    s.active_profile = "y"
    d = s.to_dict()
    assert d["schema"] == 2 and d["active_profiles"] == {"naga_v2_pro": "y"}
    assert d["active_profile"] == "y"  # I1: still readable by a running 0.2.x process
    assert Settings.from_dict(d) == s
    assert Settings.from_dict({"active_profile": "z"}).active_profile == "z"


def test_device_settings_roundtrip_and_defaults(tmp_path):
    from razertux.config import ConfigStore, DeviceSettings
    from razertux.hw.button_maps import InputSignal
    from razertux.hw.buttons import ButtonId
    s = ConfigStore(tmp_path)
    naga = s.for_model("naga_v2_pro")
    assert naga.load_device_settings() == DeviceSettings(plate=12, remap_enabled=True, learned={})
    assert s.for_model("viper").load_device_settings().remap_enabled is False
    ds = DeviceSettings(plate=6, remap_enabled=False,
                        learned={ButtonId.LEARN_1: InputSignal("evdev", 1, 275)})
    naga.save_device_settings(ds)
    assert (tmp_path / "devices" / "naga_v2_pro.json").exists()
    assert naga.load_device_settings() == ds
    assert json.loads((tmp_path / "devices" / "naga_v2_pro.json").read_text())["learned"] == \
        {"learn_1": {"source": "evdev", "type": 1, "code": 275}}


def test_default_profile_for_other_mouse_is_all_default_action():
    from razertux.config import DefaultAction, default_profile_for
    from razertux.devices.db import load_devices
    p = default_profile_for(load_devices()["viper"])
    assert p.standard and all(isinstance(a, DefaultAction) for a in p.standard.values())


def test_settings_last_model_roundtrip():
    assert Settings().last_model == ""
    s = Settings()
    s.last_model = "death_adder_v2"
    assert Settings.from_dict(s.to_dict()).last_model == "death_adder_v2"
    assert Settings.from_dict({"active_profile": "p"}).last_model == ""


def test_profile_scroll_roundtrip_and_old_files():
    from razertux.config.model import Profile, validate
    p = Profile(id="p", name="P", scroll={"mode": 1, "acceleration": False, "smart_reel": True})
    q = Profile.from_dict(p.to_dict())
    assert q.scroll == {"mode": 1, "acceleration": False, "smart_reel": True}
    d = p.to_dict()
    del d["scroll"]
    assert Profile.from_dict(d).scroll == {}  # older files: leave the mouse as is
    assert "scroll" not in Profile(id="p", name="P").to_dict()
    assert validate(p) == []
    assert validate(Profile(id="p", name="P", scroll={"mode": 2})) != []
