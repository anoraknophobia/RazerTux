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
    assert lay["buttons"]["dpi_up"]["element"] == "TODO"  # not in the template: placeholder, never invented
    assert "plate_buttons" not in lay


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
    assert set(s["layout"]["buttons"]) == {"left", "right", "middle", "wheel_up", "wheel_down", "back", "forward", "dpi_up"}
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
    assert {p.name for p in out.iterdir()} == {"device_override.json", "button_map.json", "button_map.py.txt", "layout.json"}
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


def test_daemon_source_names():
    assert imp.daemon_source("if2:keyboard") == ("if02", True)
    assert imp.daemon_source("if0:mouse") == ("mouse", True)
    assert imp.daemon_source("if1:mouse") == ("if01_mouse", True)
    assert imp.daemon_source("if1:consumer") == ("if01_event", False)


def test_entries_use_daemon_sources_and_snippet_is_valid_python():
    from evdev import ecodes as E

    from razertux.hw.button_maps import ButtonMap, InputSignal
    from razertux.hw.buttons import ButtonId
    s = imp.suggest(load("known"))["button_map"]
    by = {e["signal"]: e for e in s["signals"]}
    assert by["if2:keyboard:KEY_1"]["daemon_source"] == "if02"
    assert by["if0:mouse:REL_WHEEL:+"]["daemon_source"] == "mouse"
    snippet = imp.python_snippet(s["signals"])
    assert "InputSignal('if02', E.EV_KEY, E.KEY_1): ButtonId.SIDE_1," in snippet
    ns = {"E": E, "InputSignal": InputSignal, "ButtonId": ButtonId, "ButtonMap": ButtonMap}
    exec(snippet, ns)  # noqa: S102
    assert ns["SUGGESTED"][InputSignal("if02", E.EV_KEY, E.KEY_1)] == ButtonId.SIDE_1
    assert not any(sig.ev_type == E.EV_REL for sig in ns["SUGGESTED"])
    assert "EV_REL" not in snippet
    note = next(ln for ln in snippet.splitlines() if ln.startswith("# wheel/tilt: handled automatically"))
    assert {"wheel_up", "wheel_down"} <= set(note.split("confirmed: ")[1].rstrip(")").split(", "))


def test_snippet_strips_control_characters():
    e = {"signal": "x", "source": "s", "daemon_source": "if02\x1b[31m", "source_certain": True,
         "ev_type": 1, "code": 2, "code_name": "KEY_1", "button": "side_1"}
    assert "\x1b" not in imp.python_snippet([e])


def test_local_identity_does_not_affect_validation(monkeypatch):
    from razertux.recorder import report as r
    rep = json.loads((DATA / "known.json").read_text())
    rep["device"]["usb_product"] = "Razer Naga Trinity"
    monkeypatch.setattr(r, "_user", lambda: "naga")
    assert r.sanitize(rep)["device"]["usb_product"] != rep["device"]["usb_product"]  # recorder scrubs it
    assert imp.validate(rep) is rep  # the importer does not


def test_refuses_overwrite_without_force(tmp_path, capsys):
    out = tmp_path / "o"
    assert imp.main([str(DATA / "known.json"), "-o", str(out)]) == 0
    assert (out / "button_map.py.txt").exists()
    assert imp.main([str(DATA / "known.json"), "-o", str(out)]) == 1
    assert "--force" in capsys.readouterr().err
    assert imp.main([str(DATA / "known.json"), "-o", str(out), "--force"]) == 0


def test_refuses_output_inside_repo(capsys):
    assert imp.main([str(DATA / "known.json"), "-o", str(ROOT / "docs-out")]) == 1
    assert not (ROOT / "docs-out").exists()
    assert "repository" in capsys.readouterr().err


REPORT = (DATA / "keyboard_free.json").read_text()


@pytest.mark.parametrize("body", [
    "x\r\n```json\r\n" + REPORT.replace("\n", "\r\n") + "\r\n```\r\n",
    "```\n" + REPORT + "\n```\n",
    "~~~json\n" + REPORT + "\n~~~\n",
    "text\n\n" + "".join("    " + line + "\n" for line in REPORT.splitlines()),
    "```json\n{\"a\": 1}\n```\nnext\n```json\n" + REPORT + "\n```\n",
])
def test_fence_variants(body):
    assert imp.extract_json(body)["schema"] == "razertux-device-report"


def test_control_characters_stripped_from_output(tmp_path, capsys):
    rep = json.loads((DATA / "known.json").read_text())
    rep["notes"] = "hello \x1b[31mred"
    p = tmp_path / "r.json"
    p.write_text(json.dumps(rep))
    out = tmp_path / "o"
    imp.main([str(p), "-o", str(out)])
    assert "\x1b" not in capsys.readouterr().out


def test_plate_template_keeps_plate_buttons(monkeypatch):
    path = ROOT / "razertux/gui/assets/layouts/naga_v2_pro.json"
    tpl = json.loads(path.read_text())
    assert tpl["plate_buttons"]
    monkeypatch.setattr(imp, "_template_file", lambda choice: path)
    rep = load("known")
    lay = imp.suggest(rep)["layout"]
    assert lay["plate_buttons"] == tpl["plate_buttons"]
    assert "side_1" in lay["buttons"] and lay["buttons"]["side_1"] == tpl["plate_buttons"]["12"]["side_1"]
