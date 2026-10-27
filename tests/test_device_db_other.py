"""Device database for non-mouse Razer devices (keyboards, mats, headsets, accessories ...)."""
import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from razertux.devices import db
from razertux.devices.db import by_pid, by_pid_any, kind_by_pid, load_all_devices, load_devices
from razertux.razer.commands import BUILDERS

ROOT = Path(__file__).resolve().parent.parent
CLONE = Path("/tmp/claude-1000/-home-anoraknophobia-REA/26c3406c-5a53-44d1-89a7-08bb13495a0c/scratchpad/ext/openrazer")
MICE_PATH = ROOT / "razertux" / "devices" / "openrazer_mice.json"
OTHER_PATH = ROOT / "razertux" / "devices" / "openrazer_other.json"
MICE_SHA256 = "fa40c4abbd75006324dbd1ac86de42900ccf5969131584e0483dc407ec98b4c2"
KINDS = {"mouse", "keyboard", "keypad", "headset", "mousemat", "accessory", "dock", "other"}
HARDWARE = ("keyboards", "accessory", "headsets", "mouse_mat", "core", "monitor")

_spec = importlib.util.spec_from_file_location("import_openrazer", ROOT / "tools" / "import_openrazer.py")
imp = importlib.util.module_from_spec(_spec)
sys.modules["import_openrazer"] = imp
_spec.loader.exec_module(imp)

needs_clone = pytest.mark.skipif(not CLONE.exists(), reason="OpenRazer reference clone missing")


def test_mice_json_is_unchanged():
    assert hashlib.sha256(MICE_PATH.read_bytes()).hexdigest() == MICE_SHA256


def test_load_devices_stays_mice_only():
    devs = load_devices()
    assert devs and all(d.kind == "mouse" for d in devs.values())
    assert "firefly" not in devs and "huntsman" not in devs


def test_load_all_devices_is_mice_plus_others():
    mice, everything = load_devices(), load_all_devices()
    assert set(mice) < set(everything)
    assert all(everything[m] == d for m, d in mice.items())
    assert {d.kind for d in everything.values()} <= KINDS
    assert {"mouse", "keyboard", "keypad", "headset", "mousemat", "accessory", "dock"} <= {d.kind for d in everything.values()}


def test_no_pid_belongs_to_two_models():
    seen: dict[int, str] = {}
    for model, d in load_all_devices().items():
        for pid in d.pids:
            assert pid not in seen, f"{pid:#06x} in {seen[pid]} and {model}"
            seen[pid] = model


def test_huntsman_is_keyboard_with_firmware_read():
    d = load_all_devices()["huntsman"]
    assert d.kind == "keyboard"
    assert d.protocol["get_firmware"].fn == "razer_chroma_standard_get_firmware_version"
    assert d.protocol["get_firmware"].txn == 0xFF
    assert "get_serial" in d.protocol


def test_blackwidow_chroma_static_effect_keeps_per_model_txn():
    d = load_all_devices()["black_widow_chroma"]
    assert d.kind == "keyboard"
    c = d.for_pid(next(iter(d.pids))).protocol["set_effect:matrix:static"]
    assert c.fn == "razer_chroma_standard_matrix_effect_static" and c.txn == 0xFF


def test_firefly_is_mousemat():
    assert load_all_devices()["firefly"].kind == "mousemat"
    assert kind_by_pid(0x0C00) == "mousemat"
    assert by_pid_any(0x0C00).model == "firefly"


def test_kind_lookup_and_mouse_compat():
    assert kind_by_pid(0x00A8) == "mouse"
    assert by_pid_any(0x00A8) is by_pid(0x00A8)
    assert kind_by_pid(0xFFFF) is None and by_pid_any(0xFFFF) is None
    assert by_pid(0x0C00) is None  # by_pid stays mice-only


def test_docks_and_core():
    assert kind_by_pid(0x007E) == "dock"  # Razer Mouse Dock
    assert kind_by_pid(0x0215) == "other"  # Razer Core


def test_headsets_are_identity_only():
    heads = [d for d in load_all_devices().values() if d.kind == "headset"]
    assert len(heads) >= 5
    # the Kitty Edition is a headset served by the accessory driver (razerchromacommon reports)
    for d in (d for d in heads if d.model != "kraken_kitty_edition"):
        assert d.protocol == {} and d.pid_protocol == {}


def test_static_effect_commands_only_use_ported_builders():
    for model, d in load_all_devices().items():
        if d.kind == "mouse":
            continue
        for pid in d.pids:
            for key, c in d.for_pid(pid).protocol.items():
                for step in c.steps:
                    assert step in BUILDERS, f"{model} {key}: {step} is not ported"


def test_default_kind_for_old_json():
    d = db._from_json(json.loads(MICE_PATH.read_text())["devices"][0])
    assert d.kind == "mouse"


def test_kind_from_methods():
    assert imp.device_kind(["get_device_type_keyboard"], "RazerX") == "keyboard"
    assert imp.device_kind(["get_device_type_accessory"], "RazerMouseDockPro") == "dock"
    assert imp.device_kind(["get_device_type_accessory"], "RazerThunderbolt4DockChroma") == "dock"
    assert imp.device_kind(["get_device_type_core"], "RazerCore") == "other"
    assert imp.device_kind(["get_device_type_mousemat"], "RazerFirefly") == "mousemat"


def _clone_classes():
    out = {}
    for mod in HARDWARE:
        tree = ast.parse((CLONE / "daemon/openrazer_daemon/hardware" / f"{mod}.py").read_text())
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        for name in classes:
            pid = imp._class_attr(classes, name, "USB_PID")
            if pid is not None:
                out[name] = pid
    return out


@needs_clone
def test_every_openrazer_class_appears_once():
    classes = _clone_classes()
    assert len(classes) > 100
    db_pids = [pid for d in load_all_devices().values() if d.kind != "mouse" for pid in d.pids]
    assert sorted(db_pids) == sorted(set(classes.values()))
    assert len(db_pids) == len(set(db_pids))


@needs_clone
def test_other_db_is_reproducible():
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "import_openrazer.py"), str(CLONE), "--others"],
                         capture_output=True, text=True, check=True)
    assert res.stdout == OTHER_PATH.read_text()
    assert json.loads(res.stdout)["_source"]["commit"]


def test_command_args_are_resolved_constants():
    # razer_attr_write_matrix_effect_static: extended_matrix_effect_static(VARSTORE, BACKLIGHT_LED, <rgb from buf>)
    d = load_all_devices()["huntsman"]
    c = d.for_pid(next(iter(d.pids))).protocol["set_effect:matrix:static"]
    assert c.fn == "razer_chroma_extended_matrix_effect_static"
    assert c.args == (0x01, 0x05, "*")
    assert db.Command("x", 1).args == ()


def test_blade_laptops_use_blade_brightness_and_have_no_usb_serial():
    blades = [d for d in load_all_devices().values() if d.kind == "keyboard" and d.model.startswith("blade_stealth")]
    assert blades
    for d in blades:
        for pid in d.pids:
            p = d.for_pid(pid).protocol
            assert "get_serial" not in p  # razerkbd reads the serial from DMI for these
            if "get_brightness:matrix" in p:
                assert p["get_brightness:matrix"].fn == "razer_chroma_misc_get_blade_brightness"


def test_importer_resolves_constants_and_blade_branches():
    consts = imp.parse_constants("#define VARSTORE 0x01\n#define BACKLIGHT_LED 0x05\nenum { CLASSIC_EFFECT_STATIC = 0x00, TWO_X = 3 };")
    assert consts == {"VARSTORE": 1, "BACKLIGHT_LED": 5, "CLASSIC_EFFECT_STATIC": 0, "TWO_X": 3}
    bre = imp.builder_re(None)
    assert imp.call_args("a = razer_chroma_x_y(VARSTORE, BACKLIGHT_LED, (struct razer_rgb*)&buf[0]);", bre, consts) == (1, 5, "*")
    src = "if (is_blade_laptop(device)) { A(); } else { B(); } z();"
    assert imp.resolve_blade(src, True).split() == "{ A(); } z();".split()
    assert imp.resolve_blade(src, False).split() == "{ B(); } z();".split()


def _static(model_pid):
    return by_pid_any(model_pid).for_pid(model_pid)


def test_storage_index_is_recorded():
    c = _static(0x0227).protocol["set_effect:matrix:static"]
    assert c.storage_index == 0
    assert _static(0x0227).protocol["get_firmware"].storage_index is None
    assert db.Command("x", 1).storage_index is None
    ext_get = _static(0x0227).protocol["get_brightness:matrix"]
    assert ext_get.storage_index == 0


def test_light_test_command_places_nostore():
    cmd, args = db.light_test_command(_static(0x0227))
    assert cmd.fn == "razer_chroma_extended_matrix_effect_static"
    assert args == (0x00, 0x05, "*")  # NOSTORE at storage_index, colour left to the caller


def test_light_test_command_refuses_uncertain_cases():
    assert db.light_test_command(_static(0x02CF)) is None  # runtime storage byte
    v2 = load_all_devices()["black_widow_chroma_v2"]
    assert db.light_test_command(v2.for_pid(next(iter(v2.pids)))) is None  # no storage byte
    classic = load_all_devices()["black_widow_stealth"]
    assert db.light_test_command(classic.for_pid(next(iter(classic.pids)))) is None  # set_led_effect
    assert db.light_test_command(_static(0x00A8)) is None  # mouse without the key


def test_device_kind_rejects_unknown_types():
    with pytest.raises(ValueError):
        imp.device_kind(["get_device_type_toaster"], "RazerX")
    with pytest.raises(ValueError):
        imp.device_kind(["set_static_effect"], "RazerX")
