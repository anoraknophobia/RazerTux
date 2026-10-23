import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data" / "reports"
_spec = importlib.util.spec_from_file_location("import_device_report", ROOT / "tools" / "import_device_report.py")
imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(imp)


def load(name):
    return imp.load_report(DATA / f"{name}.json")


def test_known_mouse_button_map():
    s = imp.suggest(load("known"))
    m = s["button_map"]["map"]
    assert m["if2:keyboard:KEY_1"] == "side_1"
    assert m["if0:mouse:REL_WHEEL:+"] == "wheel_up"
    assert m["if0:mouse:REL_WHEEL:-"] == "wheel_down"
    assert "if0:mouse:BTN_EXTRA" not in m
    assert s["button_map"]["review"] == ["if0:mouse:BTN_EXTRA"]


def test_known_layout_omits_absent_and_keeps_template():
    lay = imp.suggest(load("known"))["layout"]
    assert lay["template"] == "template_mmo"
    assert "side_3" not in lay["buttons"] and "side_1" in lay["buttons"]
    assert "dpi_up" not in lay["buttons"]  # not in the template: reported as a note, never invented


def test_device_override():
    o = imp.suggest(load("known"))["device_override"]
    assert (o["vid"], o["pid"], o["kind"], o["connection"], o["name"]) == (0x1532, 0x67, "mouse", "wired", "Razer Naga Trinity")
    assert o["reads"]["dpi"]["value"] == 800 and "battery" not in o["reads"]
    assert o["light_zones"] == ["logo"]


def test_unknown_mouse_user_kind_is_unverified():
    s = imp.suggest(load("unknown_mouse"))
    o = s["device_override"]
    assert o["kind"] == "mouse" and o["unverified_user_kind"] == "mouse"
    assert s["layout"]["template"] == "template_ergo"
    assert set(s["layout"]["buttons"]) == {"left", "right", "middle", "wheel_up", "wheel_down", "back", "forward"}
    assert any("dpi_up" in n for n in s["button_map"]["notes"])
    assert s["button_map"]["map"]["if0:mouse:BTN_SIDE"] == "back"


def test_free_signals_learn_in_order_and_wheel_by_sign():
    s = imp.suggest(load("keyboard_free"))
    m = s["button_map"]["map"]
    assert m["if1:keyboard:KEY_F13"] == "learn_1"
    assert m["if1:keyboard:KEY_F14"] == "learn_2"
    assert m["if0:mouse:REL_HWHEEL:+"] == "wheel_right"
    assert "if1:keyboard:KEY_F15" not in m and s["button_map"]["review"] == ["if1:keyboard:KEY_F15"]
    assert "template" not in s["layout"] or s["layout"]["template"] is None


def test_more_than_16_free_signals_go_to_review():
    rep = load("keyboard_free")
    rep["buttons"]["signals"] = [f"if1:keyboard:KEY_F{i}" for i in range(1, 21)]
    rep["buttons"]["duplicates"] = []
    m = imp.suggest(rep)
    assert m["button_map"]["map"]["if1:keyboard:KEY_F16"] == "learn_16"
    assert m["button_map"]["review"] == [f"if1:keyboard:KEY_F{i}" for i in range(17, 21)]


def test_wrong_version_rejected():
    with pytest.raises(imp.ImportError_, match="version"):
        load("invalid_version")


def test_wrong_schema_rejected(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema": "other", "version": 1}))
    with pytest.raises(imp.ImportError_, match="schema"):
        imp.load_report(p)


def test_tampered_rejected():
    with pytest.raises(imp.ImportError_, match="tamper|sanitiz"):
        load("tampered")


def test_oversize_rejected(tmp_path):
    p = tmp_path / "big.json"
    p.write_text(" " * (imp.MAX_BYTES + 1))
    with pytest.raises(imp.ImportError_, match="large"):
        imp.load_report(p)


def test_not_json_rejected(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("nope")
    with pytest.raises(imp.ImportError_, match="JSON"):
        imp.load_report(p)


def test_unknown_button_id_rejected():
    rep = load("known")
    rep["buttons"]["results"]["bogus_button"] = ["if0:mouse:BTN_TASK"]
    with pytest.raises(imp.ImportError_, match="bogus_button"):
        imp.suggest(rep)


def test_issue_body_extraction():
    body = "### Device\nx\n\n### Report (JSON)\n\n```json\n" + (DATA / "known.json").read_text() + "\n```\n\n### Notes\n```json\n{}\n```"
    assert imp.extract_json(body) == json.loads((DATA / "known.json").read_text())


def test_issue_body_without_json_block():
    with pytest.raises(imp.ImportError_, match="no JSON"):
        imp.extract_json("### Summary\nonly text")


def test_cli_writes_only_output_dir(tmp_path, capsys):
    out = tmp_path / "out"
    assert imp.main([str(DATA / "known.json"), "-o", str(out)]) == 0
    assert {p.name for p in out.iterdir()} == {"device_override.json", "button_map.json", "layout.json"}
    assert "Razer Naga Trinity" in capsys.readouterr().out


def test_cli_reads_issue_body_file(tmp_path):
    body = tmp_path / "issue.md"
    body.write_text("### Report (JSON)\n```json\n" + (DATA / "keyboard_free.json").read_text() + "\n```\n")
    out = tmp_path / "o"
    assert imp.main([str(body), "-o", str(out)]) == 0
    assert (out / "button_map.json").exists()


def test_cli_rejects_without_writing(tmp_path, capsys):
    out = tmp_path / "out"
    assert imp.main([str(DATA / "tampered.json"), "-o", str(out)]) == 1
    assert not out.exists()
    assert "refused" in capsys.readouterr().err
