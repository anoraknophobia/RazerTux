import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from razertux.devices.db import Command

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "data"
CLONE = Path("/tmp/claude-1000/-home-anoraknophobia-REA/26c3406c-5a53-44d1-89a7-08bb13495a0c/scratchpad/ext/openrazer")
DB_PATH = ROOT / "razertux" / "devices" / "openrazer_mice.json"

_spec = importlib.util.spec_from_file_location("import_openrazer", ROOT / "tools" / "import_openrazer.py")
imp = importlib.util.module_from_spec(_spec)
sys.modules["import_openrazer"] = imp
_spec.loader.exec_module(imp)


def read(name):
    return (DATA / name).read_text()


PIDS = imp.parse_header(read("openrazer_snippet.h"))


def test_header_resolves_pids():
    assert PIDS["USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRED"] == 0x00A7
    assert len(PIDS) == 4


def test_switch_groups_map_pids_to_builder_and_txn():
    groups = imp.parse_switch(read("openrazer_snippet.c"), "razer_attr_write_poll_rate", PIDS)
    assert groups[0x005C] == Command("razer_chroma_misc_set_polling_rate", 0x3F)
    assert groups[0x00A8] == Command("razer_chroma_misc_set_polling_rate2", 0x1F)
    assert groups["default"].txn == 0xFF
    assert groups["default"].fn == "razer_chroma_misc_set_polling_rate"
    # early return with a device specific helper: no command
    assert 0x0084 not in groups


def test_function_level_builder_with_switch_txn():
    groups = imp.parse_switch(read("openrazer_snippet.c"), "razer_attr_read_charge_level", PIDS)
    assert groups[0x00A7] == Command("razer_chroma_misc_get_battery_level", 0x1F)


def test_wired_and_wireless_classes_merge_into_one_model():
    devs = imp.parse_daemon_classes(read("openrazer_mouse_snippet.py"))
    d = devs["naga_v2_pro"]
    assert d.pids == {0x00A7: "wired", 0x00A8: "wireless"}
    assert d.name == "Razer Naga V2 Pro"
    assert d.dpi_max == 30000
    assert d.poll_rates == (125, 500, 1000)  # OpenRazer default
    assert devs["viper_mini"].poll_rates == (125, 1000)


def test_probe_features_follow_fallthrough():
    files = imp.parse_probe(read("openrazer_snippet.c"), PIDS)
    assert files[0x00A8] >= {"charge_effect", "dpi", "logo_led_brightness", "device_mode"}
    assert "charge_effect" not in files[0x00A7]


def test_slug():
    assert imp.model_slug("RazerBasiliskV3Pro35KWireless") == "basilisk_v3_pro_35k"
    assert imp.model_slug("RazerDeathAdderV3ProWired_Alternate") == "death_adder_v3_pro"
    assert imp.model_slug("RazerBasiliskV3Pro35KPhantomGreenEditionWireless") == "basilisk_v3_pro_35k_phantom_green_edition"


def test_generated_db_is_reproducible(tmp_path):
    if not CLONE.exists():
        pytest.skip("OpenRazer reference clone missing")
    out = tmp_path / "out.json"
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "import_openrazer.py"), str(CLONE)],
                         capture_output=True, text=True, check=True)
    out.write_text(res.stdout)
    assert out.read_text() == DB_PATH.read_text()
    assert json.loads(out.read_text())["_source"]["commit"]
