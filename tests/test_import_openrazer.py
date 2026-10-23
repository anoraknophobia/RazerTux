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


def test_multi_step_body_records_all_steps_and_raw_prelude():
    groups = imp.parse_switch(read("openrazer_snippet.c"), "razer_attr_write_matrix_effect_static", PIDS)
    c = groups[0x00A7]
    assert c.fn == "razer_chroma_standard_set_led_state" and c.txn == 0x1F
    assert c.steps == ("raw:0x0f/0x02/0x06", "razer_chroma_standard_set_led_state",
                       "razer_chroma_standard_set_led_rgb")
    # single builder -> one step; default group returns without builder -> nothing
    assert groups[0x005C].steps == ("razer_chroma_standard_matrix_effect_static",)
    assert "default" not in groups


MINI_HEADER = """
#define USB_DEVICE_ID_RAZER_TEST_WIRED 0x0001
#define USB_DEVICE_ID_RAZER_TEST_WIRELESS 0x0002
"""
MINI_C = """
static ssize_t razer_attr_read_dpi(struct device *dev, struct device_attribute *attr, char *buf)
{
    request = razer_chroma_misc_get_dpi_xy(VARSTORE);
    switch (device->usb_pid) {
    case USB_DEVICE_ID_RAZER_TEST_WIRED:
        request.transaction_id.id = 0x3f;
        break;
    case USB_DEVICE_ID_RAZER_TEST_WIRELESS:
        request.transaction_id.id = 0x1f;
        break;
    }
    return 0;
}
static ssize_t razer_attr_read_poll_rate(struct device *dev, struct device_attribute *attr, char *buf)
{
    request = razer_chroma_misc_get_polling_rate();
    request.transaction_id.id = 0x1f;
    return 0;
}
static ssize_t razer_attr_read_charge_level(struct device *dev, struct device_attribute *attr, char *buf)
{
    request = razer_chroma_misc_get_battery_level();
    request.transaction_id.id = 0x1f;
    return 0;
}
static int razer_mouse_probe(struct hid_device *hdev, const struct hid_device_id *id)
{
    switch(dev->usb_pid) {
    case USB_DEVICE_ID_RAZER_TEST_WIRELESS:
        CREATE_DEVICE_FILE(&hdev->dev, &dev_attr_charge_level);
        fallthrough;
    case USB_DEVICE_ID_RAZER_TEST_WIRED:
        CREATE_DEVICE_FILE(&hdev->dev, &dev_attr_dpi);
        CREATE_DEVICE_FILE(&hdev->dev, &dev_attr_poll_rate);
        break;
    }
    return 0;
}
"""
MINI_COMMON = """
struct razer_report razer_chroma_misc_get_dpi_xy(unsigned char v) { }
struct razer_report razer_chroma_misc_get_polling_rate(void) { }
struct razer_report razer_chroma_misc_get_battery_level(void) { }
"""
MINI_PY = '''
class RazerTestWired(__RazerDevice):
    """
    Class for the Razer Test (Wired)
    """
    USB_PID = 0x0001
    METHODS = ["get_poll_rate"]
    DPI_MAX = 100


class RazerTestWireless(RazerTestWired):
    """
    Class for the Razer Test (Wireless)
    """
    USB_PID = 0x0002
'''


def test_per_pid_differences_are_split_from_common(tmp_path):
    (tmp_path / "driver").mkdir()
    (tmp_path / "daemon/openrazer_daemon/hardware").mkdir(parents=True)
    (tmp_path / "driver/razermouse_driver.h").write_text(MINI_HEADER)
    (tmp_path / "driver/razermouse_driver.c").write_text(MINI_C)
    (tmp_path / "driver/razerchromacommon.c").write_text(MINI_COMMON)
    (tmp_path / "daemon/openrazer_daemon/hardware/mouse.py").write_text(MINI_PY)
    import io
    db = imp.build_db(tmp_path, stderr=io.StringIO())
    d = db["devices"][0]
    assert d["features"] == ["dpi", "poll_rate"]
    assert d["pid_features"] == {"0x0002": ["battery"]}
    assert "get_poll_rate" in d["protocol"] and "get_dpi" not in d["protocol"]
    assert d["pid_protocol"]["0x0001"]["get_dpi"]["txn"] == 0x3F
    assert d["pid_protocol"]["0x0002"]["get_dpi"]["txn"] == 0x1F
    assert d["pid_protocol"]["0x0002"]["get_battery"]["txn"] == 0x1F
    assert "get_battery" not in d["pid_protocol"]["0x0001"]

    from razertux.devices.db import _from_json
    info = _from_json(d)
    assert "battery" not in info.features
    assert "battery" not in info.for_pid(0x0001).features
    assert "battery" in info.for_pid(0x0002).features
    assert info.for_pid(0x0001).protocol["get_dpi"].txn == 0x3F
    assert info.for_pid(0x0002).protocol["get_dpi"].txn == 0x1F
    assert "get_battery" not in info.for_pid(0x0001).protocol
