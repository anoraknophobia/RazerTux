#!/usr/bin/env python3
"""Maintainer tool: turn a RazerTux device report into suggestions for the device database.

Usage: python tools/import_device_report.py <issue-body-or-report-file> [-o OUTDIR]

The input is either the raw JSON report or the text of a GitHub issue (the JSON is taken from the
```json block). The report is validated (schema, version, size, and that sanitizing it changes
nothing). Four suggestion files are written to OUTDIR (default ./device-report-out):

  device_override.json  fields for the device database (kind, name, connection, confirmed reads, light zones)
  button_map.json       signal -> ButtonId; guided results by button, free signals as learn_1..learn_16,
                        wheel signals by sign; ambiguous signals are listed under "review", not mapped
  button_map.py.txt     paste-ready InputSignal(...): ButtonId.X lines in the style of razertux/hw/button_maps.py
                        (button_map.json is only an aid for humans; source names are daemon names there)
  layout.json           skeleton: template views + the buttons that are present on the device

This tool never edits repository files; review the output and copy what is right by hand.
The ``user_kind`` of a report is the reporter's own answer and is passed on as unverified.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evdev import ecodes as E  # noqa: E402

from razertux.hw.buttons import ButtonId  # noqa: E402
from razertux.recorder.recording import LAYOUTS, TEMPLATE_CHOICES  # noqa: E402
from razertux.recorder.report import SCHEMA, VERSION, sanitize, summary_text  # noqa: E402

MAX_BYTES = 1_000_000
LEARN = [f"learn_{i}" for i in range(1, 17)]
_WHEEL = {("REL_WHEEL", 1): "wheel_up", ("REL_WHEEL", -1): "wheel_down",
          ("REL_WHEEL_HI_RES", 1): "wheel_up", ("REL_WHEEL_HI_RES", -1): "wheel_down",
          ("REL_HWHEEL", 1): "wheel_right", ("REL_HWHEEL", -1): "wheel_left",
          ("REL_HWHEEL_HI_RES", 1): "wheel_right", ("REL_HWHEEL_HI_RES", -1): "wheel_left"}
_FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})[^\n]*\n(.*?)\n[ ]{0,3}\1[`~]*[ \t]*$", re.S | re.M)
_INDENT = re.compile(r"(?:^(?:    |\t)[^\n]*(?:\n|$)|^[ \t]*\n)+", re.M)
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_CODE = re.compile(r"^CODE_(\d+)_(\d+)$")


class ImportError_(Exception):
    """The report is refused."""


def extract_json(text: str) -> dict:
    """The report object from an issue body (```json block) or from plain JSON text."""
    if len(text.encode("utf-8", "replace")) > MAX_BYTES:
        raise ImportError_("input too large")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [m[1] for m in _FENCE.findall(text)]
    blocks += [re.sub(r"(?m)^(?:    |\t)", "", m.group(0)) for m in _INDENT.finditer(text) if m.group(0).strip()]
    if not blocks:
        raise ImportError_("no JSON report found")
    last = None
    for b in blocks:
        try:
            data = json.loads(b)
        except ValueError:
            last = "block is not valid JSON"
            continue
        if isinstance(data, dict) and "schema" in data:
            return data
    raise ImportError_(f"no JSON report found ({last or 'no block has a schema field'})")


def validate(data: dict) -> dict:
    if data.get("schema") != SCHEMA:
        raise ImportError_(f"wrong schema {str(data.get('schema'))[:40]!r}, expected {SCHEMA!r}")
    v = data.get("version")
    if type(v) is not int or v != VERSION:
        raise ImportError_(f"unsupported report version {str(v)[:20]!r}, expected {VERSION}")
    if sanitize(data, local_identity=False) != data:
        raise ImportError_("report is tampered or not sanitized (sanitizing it changes its content)")
    return data


def load_report(path) -> dict:
    p = Path(path)
    if p.stat().st_size > MAX_BYTES:
        raise ImportError_("input too large")
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ImportError_("input is not UTF-8 text") from None
    try:
        data = extract_json(text)
    except ImportError_ as e:
        if "not valid JSON" in str(e) or "no JSON" in str(e):
            raise ImportError_(f"input is not valid JSON ({e})") from None
        raise
    return validate(data)


def parse_signal(name: str) -> tuple[str, int, int, int | None] | None:
    """'if2:keyboard:KEY_1' / 'if0:mouse:REL_WHEEL:+' -> (source, ev_type, code, sign)."""
    parts = name.split(":")
    sign = None
    if parts[-1] in ("+", "-"):
        sign = 1 if parts.pop() == "+" else -1
    if len(parts) < 2:
        return None
    source, sig = ":".join(parts[:-1]), parts[-1]
    m = _CODE.match(sig)
    if m:
        return source, int(m[1]), int(m[2]), sign
    for prefix, ev in (("KEY_", E.EV_KEY), ("BTN_", E.EV_KEY), ("REL_", E.EV_REL), ("ABS_", E.EV_ABS)):
        if sig.startswith(prefix) and isinstance(getattr(E, sig, None), int):
            return source, ev, getattr(E, sig), sign
    return None


_NODE = re.compile(r"^if(\d+):(\w+)$")


def daemon_source(report_source: str) -> tuple[str, bool]:
    """Report source ('if2:keyboard') -> daemon source name ('if02'), plus whether the guess is solid.

    Follows razertux/daemon/devices.py: mouse node of interface 0 -> 'mouse', mouse node of ifN ->
    'ifNN_mouse', keyboard node of ifN -> 'ifNN'; other nodes are guessed as 'ifNN_event'."""
    m = _NODE.match(report_source)
    if not m:
        return report_source, False
    n, node = int(m[1]), m[2]
    if node == "mouse":
        return ("mouse" if n == 0 else f"if{n:02d}_mouse"), True
    if node == "keyboard":
        return f"if{n:02d}", True
    return f"if{n:02d}_event", False


def python_snippet(entries: list[dict]) -> str:
    """Ready-to-paste table lines in the style of razertux/hw/button_maps.py."""
    lines = ["# Suggested by tools/import_device_report.py - UNVERIFIED, check each line on hardware.",
             "SUGGESTED: ButtonMap = {"]
    for e in entries:
        ev = {E.EV_KEY: "E.EV_KEY", E.EV_REL: "E.EV_REL", E.EV_ABS: "E.EV_ABS"}.get(e["ev_type"], str(e["ev_type"]))
        code = e["code_name"] if e["code_name"] and not e["code_name"].startswith("CODE_") else str(e["code"])
        code = f"E.{code}" if not code.isdigit() else code
        todo = "" if e["source_certain"] else "  # TODO: check the source name"
        lines.append(f"    InputSignal({e['daemon_source']!r}, {ev}, {code}): ButtonId.{ButtonId(e['button']).name},{todo}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _wheel_button(name: str) -> str | None:
    p = parse_signal(name)
    if p is None or p[1] != E.EV_REL or p[3] is None:
        return None
    base = name.rsplit(":", 1)[0].rsplit(":", 1)[-1]
    return _WHEEL.get((base, p[3]))


def _template_file(choice) -> Path | None:
    stem = TEMPLATE_CHOICES.get(choice) if isinstance(choice, str) else None
    return LAYOUTS / f"{stem}.json" if stem else None


def suggest(report: dict) -> dict:
    dev, b = report["device"], report["buttons"]
    dups = set(b.get("duplicates", []))
    valid = {x.value for x in ButtonId}
    notes: list[str] = []
    mapping: dict[str, str] = {}
    review: list[str] = []
    present: list[str] = []

    for button, val in b.get("results", {}).items():
        if button not in valid:
            raise ImportError_(f"report names unknown button id {button!r}")
        if val == "absent":
            continue
        present.append(button)
        for sig in val:
            if sig in dups:
                if sig not in review:
                    review.append(sig)
            elif mapping.get(sig, button) != button:
                notes.append(f"{sig} appears on {mapping[sig]} and {button}; not mapped")
                mapping.pop(sig)
                review.append(sig)
            elif sig in review:
                continue
            else:
                mapping[sig] = button
    free_learn = iter(n for n in LEARN if n not in present)
    for sig in b.get("signals", []):
        if sig in mapping or sig in review:
            continue
        if sig in dups:
            review.append(sig)
            continue
        wheel = _wheel_button(sig)
        target = wheel or next(free_learn, None)
        if target is None or target in mapping.values():
            review.append(sig)
            continue
        mapping[sig] = target
    entries = []
    for sig, button in mapping.items():
        p = parse_signal(sig)
        if p is None:
            notes.append(f"{sig}: unrecognized signal name, not mapped")
            continue
        src, certain = daemon_source(p[0])
        raw = sig.rsplit(":", 1)[0] if p[3] is not None else sig
        entries.append({"signal": sig, "source": p[0], "daemon_source": src, "source_certain": certain,
                        "ev_type": p[1], "code": p[2], "code_name": raw.rsplit(":", 1)[-1], "button": button})
    mapping = {e["signal"]: e["button"] for e in entries}
    if b.get("timed_out"):
        notes.append(f"recording timed out at {b.get('stopped_at', '?')}; later buttons were not recorded")

    tfile = _template_file(b.get("template"))
    layout: dict = {"template": tfile.stem if tfile else None, "views": [], "buttons": {}}
    if tfile and tfile.exists():
        tpl = json.loads(tfile.read_text(encoding="utf-8"))
        layout["views"] = tpl.get("views", [])
        plates = tpl.get("plate_buttons", {})
        if plates:
            layout["plate_buttons"] = plates
        known_specs = {**{k: v for t in plates.values() for k, v in t.items()}, **tpl["buttons"]}
        view0 = layout["views"][0]["name"] if layout["views"] else "TODO"
        for button in present:
            if button in known_specs:
                layout["buttons"][button] = known_specs[button]
            else:
                layout["buttons"][button] = {"view": view0, "element": "TODO", "side": "TODO", "todo": True}
                notes.append(f"{button} is not in {tfile.stem}; added to layout.json with a TODO element")
    elif b.get("template"):
        notes.append(f"unknown template {str(b['template'])[:20]!r}")

    override: dict = {k: dev[k] for k in ("vid", "pid") if k in dev}
    if "usb_product" in dev:
        override["name"] = dev["usb_product"]
    for k in ("kind", "connection", "db_model"):
        if k in dev:
            override[k] = dev[k]
    if "user_kind" in dev:
        override["unverified_user_kind"] = dev["user_kind"]
    override["reads"] = {k: {x: v[x] for x in ("value", "raw") if x in v}
                         for k, v in report["reads"].items() if v.get("status") == "ok" and "value" in v}
    light = report["light"]
    override["light_zones"] = sorted(k for k, v in light.items() if v == "yes") if isinstance(light, dict) else []
    return {"device_override": override,
            "button_map": {"map": mapping, "signals": entries, "review": review, "notes": notes},
            "layout": layout}


def write_suggestions(sug: dict, outdir: Path, force: bool = False) -> list[Path]:
    out = outdir.resolve()
    if out == ROOT or ROOT in out.parents:
        raise ImportError_("refusing to write inside the repository; choose an output directory outside it")
    files = {f"{n}.json": json.dumps(sug[n], indent=1) + "\n" for n in ("device_override", "button_map", "layout")}
    files["button_map.py.txt"] = python_snippet(sug["button_map"]["signals"])
    if not force:
        existing = [n for n in files if (out / n).exists()]
        if existing:
            raise ImportError_(f"{', '.join(existing)} already exist in {out}; use --force to overwrite")
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in files.items():
        p = out / name
        p.write_text(text, encoding="utf-8")
        paths.append(p)
    return paths


def _clean(text: str) -> str:
    return _CTRL.sub("?", text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", help="issue body text file or report JSON file")
    ap.add_argument("-o", "--output", default="device-report-out",
                    help="output directory for the suggestions (must be outside the repository)")
    ap.add_argument("--force", action="store_true", help="overwrite existing output files")
    a = ap.parse_args(argv)
    try:
        report = load_report(a.input)
        sug = suggest(report)
        print(_clean(summary_text(report)))
        paths = write_suggestions(sug, Path(a.output), a.force)
    except (ImportError_, OSError) as e:
        print(_clean(f"refused: {e}"), file=sys.stderr)
        return 1
    bm = sug["button_map"]
    print(_clean(f"\nMapped {len(bm['map'])} signal(s); {len(bm['review'])} for manual review: {', '.join(bm['review']) or '-'}"))
    for n in bm["notes"]:
        print(_clean(f"note: {n}"))
    for p in paths:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
