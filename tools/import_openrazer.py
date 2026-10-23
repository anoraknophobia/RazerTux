#!/usr/bin/env python3
"""Generate razertux/devices/openrazer_mice.json from an OpenRazer checkout.

Usage: python tools/import_openrazer.py <openrazer-clone> > razertux/devices/openrazer_mice.json
       python tools/import_openrazer.py <openrazer-clone> --others > razertux/devices/openrazer_other.json
       python tools/import_openrazer.py --udev > packaging/70-razertux.rules

The OpenRazer sources are only read as text (``ast`` for the daemon classes, a small
brace-matching scanner for the C driver); nothing from them is imported or executed.
Source data: OpenRazer (GPL-2.0-only), https://github.com/openrazer/openrazer
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from razertux.devices.db import Command  # noqa: E402

REPO_URL = "https://github.com/openrazer/openrazer"
DEFAULT_POLL_RATES = (125, 500, 1000)  # OpenRazer device_base default when set_poll_rate exists
DEFAULT_TXN = 0xFF  # OpenRazer default transaction id

# attribute (suffix of razer_attr_<read|write>_<attr>) -> protocol key
ATTR_KEYS = {
    "dpi": ("get_dpi", "set_dpi"),
    "dpi_stages": ("get_dpi_stages", "set_dpi_stages"),
    "poll_rate": ("get_poll_rate", "set_poll_rate"),
    "charge_level": ("get_battery", None),
    "charge_status": ("get_charging", None),
    "device_idle_time": ("get_idle_time", "set_idle_time"),
    "charge_low_threshold": ("get_low_battery_threshold", "set_low_battery_threshold"),
    "scroll_mode": ("get_scroll_mode", "set_scroll_mode"),
    "scroll_acceleration": ("get_scroll_acceleration", "set_scroll_acceleration"),
    "scroll_smart_reel": ("get_scroll_smart_reel", "set_scroll_smart_reel"),
    "device_mode": ("get_device_mode", "set_device_mode"),
    "firmware_version": ("get_firmware", None),
    "device_serial": ("get_serial", None),
}
# sysfs file (dev_attr_<name>) -> feature
FILE_FEATURES = {
    "dpi": "dpi", "dpi_stages": "dpi_stages", "poll_rate": "poll_rate",
    "charge_level": "battery", "charge_status": "charging", "device_idle_time": "idle_time",
    "charge_low_threshold": "low_battery_threshold", "scroll_mode": "scroll_mode",
    "scroll_acceleration": "scroll_acceleration", "scroll_smart_reel": "scroll_smart_reel",
    "device_mode": "device_mode", "firmware_version": "firmware", "device_serial": "serial",
}
KINDS = {"Wired": "wired", "Wireless": "wireless", "Receiver": "receiver", "Bluetooth": "bluetooth", "Dongle": "receiver"}
SKIP_EFFECTS = {"custom"}
KIND_TYPES = {"mouse", "keyboard", "keypad", "headset", "mousemat", "accessory", "core"}


# --------------------------------------------------------------------------- C scanning

def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def match_brace(text: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced braces")


def parse_header(text: str) -> dict[str, int]:
    return {m.group(1): int(m.group(2), 16)
            for m in re.finditer(r"#define\s+(USB_DEVICE_ID_\w+)\s+(0x[0-9A-Fa-f]+)", strip_comments(text))}


def function_body(src: str, name: str) -> str | None:
    m = re.search(r"^[^\n;{}]*\b" + re.escape(name) + r"\s*\([^;{]*?\)\s*\{", src, flags=re.M | re.S)
    if not m:
        return None
    start = m.end() - 1
    return src[start + 1:match_brace(src, start)]


PROBE_EXPR = r"(?:dev->usb_pid|usb_dev->descriptor\.idProduct)"


def top_level_switches(body: str, expr: str = r"(?:device|dev)->usb_pid"):
    """(header_start, body_text, end) for every switch on the pid that is not nested in another one."""
    out, pos = [], 0
    pat = re.compile(r"\bswitch\s*\(\s*" + expr + r"\s*\)\s*\{")
    while True:
        m = pat.search(body, pos)
        if not m:
            return out
        end = match_brace(body, m.end() - 1)
        out.append((m.start(), body[m.end():end], end))
        pos = end + 1


def split_groups(sw: str):
    """Yield (labels, text) per case group of a switch body; labels are USB_DEVICE_ID_* names or 'default'.

    Only labels at brace depth 0 start a group; consecutive labels share the following text.
    """
    labels: list[str] = []
    text: list[str] = []
    depth = 0
    groups = []
    i = 0
    label_re = re.compile(r"\s*(?:case\s+(\w+)\s*:|(default)\s*:)")
    while i < len(sw):
        if depth == 0:
            m = label_re.match(sw, i)
            if m:
                if text and "".join(text).strip():
                    groups.append((labels, "".join(text)))
                    labels, text = [], []
                labels.append(m.group(1) or "default")
                i = m.end()
                continue
        c = sw[i]
        depth += c == "{"
        depth -= c == "}"
        text.append(c)
        i += 1
    if labels:
        groups.append((labels, "".join(text)))
    return groups


def builder_re(builders):
    if builders:
        return re.compile(r"\b(" + "|".join(sorted(map(re.escape, builders))) + r")\s*\(")
    return re.compile(r"\b(razer_chroma_\w+)\s*\(")


TXN_RE = re.compile(r"transaction_id\.id\s*=\s*(0x[0-9A-Fa-f]+|\d+)")
HELPER_RE = re.compile(r"\b(razer_attr_(?:read|write)_\w+)\s*\(")


RAW_RE = re.compile(r"\bget_razer_report\s*\(\s*(0x[0-9A-Fa-f]+)\s*,\s*(0x[0-9A-Fa-f]+)\s*,\s*(0x[0-9A-Fa-f]+)\s*\)")


def parse_constants(text: str) -> dict[str, int]:
    """``#define NAME 0x..`` and ``NAME = 0x..`` enum entries of a C header."""
    text = strip_comments(text)
    out = {m[1]: int(m[2], 0) for m in re.finditer(r"#define\s+(\w+)\s+(0x[0-9A-Fa-f]+|\d+)\b", text)}
    out.update({m[1]: int(m[2], 0) for m in re.finditer(r"\b([A-Z][A-Z0-9_]+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*[,}\n]", text)})
    return out


def call_args(text: str, bre, consts: dict[str, int]) -> tuple:
    """Arguments of the first builder call in ``text``: known constants/numbers resolved, the rest "*"."""
    m = bre.search(text)
    if not m:
        return ()
    depth, cur, parts = 1, [], []
    for c in text[m.end():]:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        if c == "," and depth == 1:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    parts = [p.strip() for p in parts]
    if parts == [""]:
        return ()
    out = []
    for p in parts:
        if p in consts:
            out.append(consts[p])
        elif re.fullmatch(r"0x[0-9A-Fa-f]+|\d+", p):
            out.append(int(p, 0))
        else:
            out.append("*")
    return tuple(out)


def resolve_blade(src: str, is_blade: bool) -> str:
    """Keep one branch of every ``if (is_blade_laptop(device)) {A} else {B}``."""
    pat = re.compile(r"if\s*\(\s*is_blade_laptop\s*\(\s*device\s*\)\s*\)\s*\{")
    pos = 0
    while True:
        m = pat.search(src, pos)
        if not m:
            return src
        a_end = match_brace(src, m.end() - 1)
        e = re.compile(r"\s*else\s*\{").match(src, a_end + 1)
        if not e:
            pos = m.end()
            continue
        b_end = match_brace(src, e.end() - 1)
        keep = src[m.end() - 1:a_end + 1] if is_blade else src[e.end() - 1:b_end + 1]
        src = src[:m.start()] + keep + src[b_end + 1:]
        pos = m.start() + len(keep)


def _first_builder(text, bre):
    m = bre.search(text)
    return m.group(1) if m else None


def _steps(text, bre) -> tuple[str, ...]:
    """Every builder call plus raw ``get_razer_report(a, b, c)`` packets, in source order.

    Raw packets become ``raw:0xAA/0xBB/0xCC`` (lower case hex). Consecutive identical steps
    (the same builder in an if/else on the argument count) are collapsed.
    """
    found = [(m.start(), m.group(1)) for m in bre.finditer(text)]
    found += [(m.start(), "raw:" + "/".join(g.lower() for g in m.groups())) for m in RAW_RE.finditer(text)]
    steps: list[str] = []
    for _pos, step in sorted(found):
        if not steps or steps[-1] != step:
            steps.append(step)
    return tuple(steps)


def _first_txn(text):
    m = TXN_RE.search(text)
    return int(m.group(1), 0) if m else None


def parse_switch(src: str, func: str, pids: dict[str, int], builders=None, _depth: int = 0, consts=None):
    """Map pid (or 'default') -> Command for one driver function.

    Per pid: builder and transaction id come from its case group; a group without builder that
    leaves the function with ``return`` (device specific helper) yields no command; otherwise the
    first builder outside the pid switches is used. Specific groups beat ``default``.
    Missing transaction id -> 0xFF. If the function itself has no builder but calls another
    ``razer_attr_*`` helper, that helper is parsed instead.
    """
    src = strip_comments(src)
    body = function_body(src, func)
    if body is None:
        return {}
    bre = builder_re(builders)
    switches = top_level_switches(body)

    outside, last = [], 0
    for start, _text, end in switches:
        outside.append(body[last:start])
        last = end + 1
    outside.append(body[last:])
    outside_text = "\n".join(outside)

    def cargs(text):
        return call_args(text, bre, consts) if consts is not None else ()

    spec: dict = {}  # key -> dict(fn, txn, blocked)
    for _s, sw, _e in switches:
        for labels, text in split_groups(sw):
            fn, txn, steps = _first_builder(text, bre), _first_txn(text), _steps(text, bre)
            blocked = fn is None and re.search(r"\breturn\b", text) is not None
            for lab in labels:
                key = "default" if lab == "default" else pids.get(lab)
                if key is None:
                    continue
                cur = spec.setdefault(key, {"fn": None, "txn": None, "blocked": False, "steps": (), "args": ()})
                if fn and not cur["fn"]:
                    cur["steps"] = steps
                    cur["args"] = cargs(text)
                elif not fn and not cur["fn"] and not cur["steps"]:
                    cur["steps"] = steps  # raw prelude only; builder comes from default/fallback
                cur["fn"] = cur["fn"] or fn
                cur["txn"] = cur["txn"] if cur["txn"] is not None else txn
                cur["blocked"] = cur["blocked"] or blocked

    fallback_fn = _first_builder(outside_text, bre)
    fallback_steps = _steps(outside_text, bre)
    fallback_txn = _first_txn(outside_text)
    fallback_args = cargs(outside_text)
    if fallback_fn is None and not any(v["fn"] for v in spec.values()):
        for helper in dict.fromkeys(HELPER_RE.findall(body)):
            if helper != func and _depth < 2:
                sub = parse_switch(src, helper, pids, builders, _depth + 1, consts)
                if sub:
                    return sub
        return {}

    default = spec.get("default", {"fn": None, "txn": None, "blocked": False, "steps": (), "args": ()})
    out: dict = {}

    def resolve(key, entry):
        if entry["blocked"] and not entry["fn"]:
            return None
        fn = entry["fn"] or default["fn"] or fallback_fn
        if not fn:
            return None
        if entry["fn"]:
            steps = entry["steps"]
        else:  # no own builder: own raw prelude (if any) + inherited steps
            steps = entry["steps"] + (default["steps"] if default["fn"] else fallback_steps)
        txn = entry["txn"]
        if txn is None:
            txn = default["txn"] if default["txn"] is not None else fallback_txn
        if entry["fn"]:
            args = entry["args"]
        else:
            args = default["args"] if default["fn"] else fallback_args
        return Command(fn, DEFAULT_TXN if txn is None else txn, steps, args)

    for key, entry in spec.items():
        if key == "default":
            continue
        cmd = resolve(key, entry)
        if cmd:
            out[key] = cmd
    dcmd = resolve("default", default)
    if dcmd:
        out["default"] = dcmd
    return out


def parse_probe(src: str, pids: dict[str, int], func: str = "razer_mouse_probe",
                expr: str = r"dev->usb_pid") -> dict[int, set[str]]:
    """pid -> sysfs attribute names created in the driver's probe function (fallthrough aware)."""
    src = strip_comments(src)
    body = function_body(src, func)
    if body is None:
        return {}
    file_re = re.compile(r"CREATE_DEVICE_FILE\s*\([^,]+,\s*&dev_attr_(\w+)\s*\)")
    switches = [s for s in top_level_switches(body, expr) if "CREATE_DEVICE_FILE" in s[1]]
    common: set[str] = set()
    last = 0
    for start, _t, end in switches:
        common.update(file_re.findall(body[last:start]))
        last = end + 1
    common.update(file_re.findall(body[last:]))

    result = _probe_cumulative(switches, file_re, pids)
    for pid in result:
        result[pid] |= common
    return result


def _chain_files(groups, gi, file_re) -> set[str]:
    files, j = set(), gi
    while True:
        files |= set(file_re.findall(groups[j][1]))
        if re.search(r"\bfallthrough\b", groups[j][1]) and j + 1 < len(groups):
            j += 1
        else:
            return files


def _probe_cumulative(switches, file_re, pids):
    result: dict[int, set[str]] = {}
    for _s, sw, _e in switches:
        groups = split_groups(sw)
        for gi, (labels, _t) in enumerate(groups):
            files = _chain_files(groups, gi, file_re)
            for lab in labels:
                if lab in pids:
                    result.setdefault(pids[lab], set()).update(files)
    return result


# --------------------------------------------------------------------------- daemon classes

@dataclass
class ClassInfo:
    model: str
    name: str
    pids: dict[int, str] = field(default_factory=dict)
    dpi_max: int = 0
    poll_rates: tuple[int, ...] = ()
    kind: str = "mouse"


def device_kind(methods, class_name: str) -> str:
    """Device kind from the daemon class: the ``get_device_type_<x>`` method (docks by class name)."""
    for m in methods or ():
        t = re.match(r"get_device_type_(\w+)$", m)
        if not t:
            continue
        t = t.group(1)
        if t == "accessory":
            return "dock" if "Dock" in class_name else "accessory"
        return {"core": "other"}.get(t, t) if t in KIND_TYPES else "other"
    return "other"


def model_slug(class_name: str) -> str:
    n = re.sub(r"_Alternate$", "", class_name)
    n = re.sub(r"^Razer", "", n)
    n = re.sub(r"(WirelessReceiver|Wireless|Wired|Receiver|Bluetooth|Dongle)$", "", n)
    n = re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Z][a-z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", n)
    return n.lower()


def class_kind(class_name: str) -> str:
    n = re.sub(r"_Alternate$", "", class_name)
    for suffix in ("Receiver", "Wireless", "Wired", "Bluetooth", "Dongle"):
        if n.endswith(suffix):
            return KINDS[suffix]
    return "wired"


def _eval(node, classes, attr):
    """Tiny evaluator for the constant expressions used in class bodies."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, classes, attr) for e in node.elts]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _eval(node.left, classes, attr) + _eval(node.right, classes, attr)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in classes:
        return _class_attr(classes, node.value.id, node.attr)
    raise ValueError(ast.dump(node))


def _class_attr(classes, cname, attr):
    node = classes[cname]
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == attr for t in stmt.targets):
            return _eval(stmt.value, classes, attr)
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in classes:
            v = _class_attr(classes, base.id, attr)
            if v is not None:
                return v
    return None


def parse_daemon_classes(text: str) -> dict[str, ClassInfo]:
    tree = ast.parse(text)
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    devices: dict[str, ClassInfo] = {}
    for cname in classes:
        pid = _class_attr(classes, cname, "USB_PID")
        if pid is None:
            continue
        doc = ast.get_docstring(classes[cname]) or ""
        m = re.search(r"Class for the (Razer .*?)(?:\s*\((?:Wired|Wireless|Receiver|Bluetooth|Dongle)[^)]*\))?\s*$", doc.strip())
        name = m.group(1) if m else "Razer " + model_slug(cname).replace("_", " ").title()
        slug = model_slug(cname)
        info = devices.setdefault(slug, ClassInfo(slug, name))
        info.kind = device_kind(_class_attr(classes, cname, "METHODS"), cname) if info.pids == {} else info.kind
        info.pids[pid] = class_kind(cname)
        dpi_max = _class_attr(classes, cname, "DPI_MAX") or 0
        info.dpi_max = max(info.dpi_max, dpi_max)
        rates = _class_attr(classes, cname, "POLL_RATES")
        methods = _class_attr(classes, cname, "METHODS") or []
        if not rates and "set_poll_rate" in methods:
            rates = DEFAULT_POLL_RATES
        if rates and not info.poll_rates:
            info.poll_rates = tuple(rates)
    return devices


# --------------------------------------------------------------------------- assembly

def builder_names(common_c: str) -> set[str]:
    return set(re.findall(r"^struct\s+razer_report\s+(razer_\w+)\s*\(", strip_comments(common_c), flags=re.M))


ZONE_ATTR_RE = re.compile(r"^(?:(?P<zone>[a-z]+)_)?matrix_effect_(?P<effect>[a-z_]+?)$")


def zone_attrs(driver_src: str):
    """(zone -> {effects}, zone -> (read_attr, write_attr)) from razer_attr_* function names."""
    names = set(re.findall(r"\bstatic\s+ssize_t\s+razer_attr_(?:read|write)_(\w+)\s*\(", driver_src))
    effects: dict[str, set[str]] = {}
    brightness: dict[str, str] = {}
    for n in names:
        m = ZONE_ATTR_RE.match(n)
        if m and not n.endswith("_common") and m["effect"] not in SKIP_EFFECTS and "custom" not in n:
            effects.setdefault(m["zone"] or "matrix", set()).add(m["effect"])
        mb = re.match(r"^([a-z]+)_led_brightness$", n)
        if mb:
            brightness[mb[1]] = n
        elif n == "matrix_brightness":
            brightness["matrix"] = n
    return effects, brightness


def build_db(clone: Path, stderr=sys.stderr) -> dict:
    drv = clone / "driver"
    header = parse_header((drv / "razermouse_driver.h").read_text())
    c_src = (drv / "razermouse_driver.c").read_text()
    builders = builder_names((drv / "razerchromacommon.c").read_text())
    classes = parse_daemon_classes((clone / "daemon/openrazer_daemon/hardware/mouse.py").read_text())
    clean_src = strip_comments(c_src)
    probe = parse_probe(c_src, header)
    effects, brightness = zone_attrs(clean_src)

    # per-attribute command maps (cached)
    cmds: dict[str, dict] = {}

    def command_map(attr_func):
        if attr_func not in cmds:
            cmds[attr_func] = parse_switch(c_src, attr_func, header, builders)
        return cmds[attr_func]

    def cmd_for(attr_func, pid):
        m = command_map(attr_func)
        return m.get(pid, m.get("default")) if pid in m or "default" in m else None

    devices, fn_counts, conflicts = [], Counter(), Counter()
    order = {"wired": 0, "wireless": 1, "receiver": 2, "bluetooth": 3}
    for slug in sorted(classes):
        ci = classes[slug]
        pids = sorted(ci.pids, key=lambda p: (order[ci.pids[p]], p))
        files: set[str] = set()
        for p in pids:
            files |= probe.get(p, set())  # union only to discover zones/effects
        pid_files = {p: probe.get(p, set()) for p in pids}
        pid_feats = {p: {FILE_FEATURES[f] for f in pid_files[p] if f in FILE_FEATURES} for p in pids}
        common_feats = set.intersection(*pid_feats.values())
        zones: dict[str, list[str]] = {}
        for zone in sorted(set(effects) | set(brightness)):
            pre = zone + "_" if zone != "matrix" else ""
            has_b = (f"{zone}_led_brightness" if zone != "matrix" else "matrix_brightness") in files
            zeff = sorted(e for e in effects.get(zone, ()) if f"{pre}matrix_effect_{e}" in files)
            if has_b or zeff:
                zones[zone] = zeff

        # (protocol key, driver function, sysfs file the PID must have)
        wanted: list[tuple[str, str, str]] = []
        for attr, (rk, wk) in ATTR_KEYS.items():
            if rk:
                wanted.append((rk, f"razer_attr_read_{attr}", attr))
            if wk:
                wanted.append((wk, f"razer_attr_write_{attr}", attr))
        for zone, zeff in zones.items():
            battr = brightness.get(zone)
            if battr:
                wanted.append((f"get_brightness:{zone}", f"razer_attr_read_{battr}", battr))
                wanted.append((f"set_brightness:{zone}", f"razer_attr_write_{battr}", battr))
            for e in zeff:
                pre = "" if zone == "matrix" else zone + "_"
                wanted.append((f"set_effect:{zone}:{e}", f"razer_attr_write_{pre}matrix_effect_{e}", f"{pre}matrix_effect_{e}"))

        per_pid: dict[int, dict[str, Command]] = {p: {} for p in pids}
        for key, func, fname in wanted:
            for p in pids:
                if fname in pid_files[p]:
                    c = cmd_for(func, p)
                    if c:
                        per_pid[p][key] = c
        common_proto = {k: c for k, c in per_pid[pids[0]].items()
                        if all(per_pid[p].get(k) == c for p in pids)}
        for c in common_proto.values():
            fn_counts[c.fn] += 1

        def enc(c: Command) -> dict:
            return {"fn": c.fn, "txn": c.txn, "steps": list(c.steps)}

        pid_protocol, pid_features = {}, {}
        for p in pids:
            extra = {k: enc(c) for k, c in sorted(per_pid[p].items()) if k not in common_proto}
            if extra:
                pid_protocol[f"0x{p:04X}"] = extra
                for k, c in per_pid[p].items():
                    if k not in common_proto:
                        fn_counts[c.fn] += 1
                        conflicts[k] += 1
            if pid_feats[p] - common_feats:
                pid_features[f"0x{p:04X}"] = sorted(pid_feats[p] - common_feats)

        devices.append({
            "model": slug,
            "name": ci.name,
            "pids": {f"0x{p:04X}": ci.pids[p] for p in pids},
            "dpi_max": ci.dpi_max,
            "poll_rates": list(ci.poll_rates),
            "features": sorted(common_feats),
            "pid_features": pid_features,
            "zones": zones,
            "protocol": {k: enc(c) for k, c in sorted(common_proto.items())},
            "pid_protocol": pid_protocol,
            "interface": 0,
            "verified": slug == "naga_v2_pro",
        })

    print("builder functions used (devices):", file=stderr)
    for fn, n in sorted(fn_counts.items()):
        print(f"  {fn} ({n} Geraete)", file=stderr)
    if conflicts:
        print("per-PID command entries (not common to all PIDs of a model): "
              + ", ".join(f"{k} x{n}" for k, n in sorted(conflicts.items())), file=stderr)
    print(f"{len(devices)} models, {sum(len(d['pids']) for d in devices)} PIDs", file=stderr)

    commit = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return {
        "_source": {"repo": REPO_URL, "commit": commit, "license": "GPL-2.0-only (OpenRazer)"},
        "devices": sorted(devices, key=lambda d: d["model"]),
    }


# --------------------------------------------------------------------------- non-mouse devices

# daemon module -> kernel driver (identity PIDs come from the driver header)
OTHER_SOURCES = {
    "keyboards": "razerkbd_driver",
    "accessory": "razeraccessory_driver",
    "core": "razeraccessory_driver",
    "monitor": "razeraccessory_driver",
    "mouse_mat": "razeraccessory_driver",
    "headsets": "razerkraken_driver",
}
PROBES = {"razerkbd_driver": "razer_kbd_probe", "razeraccessory_driver": "razer_accessory_probe"}
# (protocol key, sysfs attribute) read/written device-wide; kraken (a different USB protocol) has none
OTHER_COMMANDS = (
    ("get_firmware", "razer_attr_read_firmware_version", "firmware_version"),
    ("get_serial", "razer_attr_read_device_serial", "device_serial"),
    ("get_battery", "razer_attr_read_charge_level", "charge_level"),
    ("get_charging", "razer_attr_read_charge_status", "charge_status"),
    ("get_brightness:matrix", "razer_attr_read_matrix_brightness", "matrix_brightness"),
    ("set_effect:matrix:static", "razer_attr_write_matrix_effect_static", "matrix_effect_static"),
)
OTHER_FEATURES = {**FILE_FEATURES, "matrix_brightness": "brightness", "matrix_effect_static": "static_effect"}


def blade_laptop_pids(driver_src: str, pids: dict[str, int]) -> set[int]:
    """PIDs for which razerkbd reads the serial from DMI instead of the device."""
    body = function_body(strip_comments(driver_src), "is_blade_laptop")
    return {pids[n] for n in re.findall(r"case\s+(\w+)\s*:", body or "") if n in pids}


def build_other_db(clone: Path, stderr=sys.stderr) -> dict:
    from razertux.razer.commands import BUILDERS

    drv, hw = clone / "driver", clone / "daemon/openrazer_daemon/hardware"
    builders = builder_names((drv / "razerchromacommon.c").read_text())
    consts = parse_constants((drv / "razercommon.h").read_text() + (drv / "razerchromacommon.h").read_text())
    drivers: dict[str, tuple[str, dict, dict, set]] = {}
    for name in sorted(set(OTHER_SOURCES.values())):
        src = (drv / f"{name}.c").read_text()
        header = parse_header((drv / f"{name}.h").read_text())
        probe = parse_probe(src, header, PROBES[name], PROBE_EXPR) if name in PROBES else {}
        blades = blade_laptop_pids(src, header) if name == "razerkbd_driver" else set()
        drivers[name] = (src, header, probe, blades)
    pid_driver = {pid: name for name, (_s, header, _p, _b) in drivers.items() for pid in header.values()}
    cmds: dict[tuple[str, str], dict] = {}
    skipped: Counter = Counter()

    devices, kinds, fn_counts = [], Counter(), Counter()
    seen_slugs: dict[str, str] = {}
    for module, dname in OTHER_SOURCES.items():
        classes = parse_daemon_classes((hw / f"{module}.py").read_text())
        for slug in sorted(classes):
            ci = classes[slug]
            # the driver that owns the PIDs (the Kraken Kitty Edition is a headset on the accessory driver)
            owners = {pid_driver.get(p) for p in ci.pids}
            if len(owners) != 1 or None in owners:
                print(f"warning: {slug}: PIDs not found in one driver header", file=stderr)
            dname = owners.pop() if len(owners) == 1 and None not in owners else dname
            src, header, probe, blades = drivers[dname]
            if slug in seen_slugs:
                raise ValueError(f"model slug {slug} in {module} and {seen_slugs[slug]}")
            seen_slugs[slug] = module
            order = {"wired": 0, "wireless": 1, "receiver": 2, "bluetooth": 3}
            pids = sorted(ci.pids, key=lambda p: (order[ci.pids[p]], p))
            pid_files = {p: probe.get(p, set()) for p in pids}
            pid_feats = {p: {OTHER_FEATURES[f] for f in pid_files[p] if f in OTHER_FEATURES} for p in pids}
            per_pid: dict[int, dict[str, Command]] = {p: {} for p in pids}
            if dname in PROBES:
                for key, func, fname in OTHER_COMMANDS:
                    for variant in (True, False):  # is_blade_laptop() branch taken
                        if (dname, func, variant) not in cmds:
                            vsrc = resolve_blade(src, variant) if dname == "razerkbd_driver" else src
                            cmds[(dname, func, variant)] = parse_switch(vsrc, func, header, builders, consts=consts)
                    for p in pids:
                        if fname not in pid_files[p] or (key == "get_serial" and p in blades):
                            continue
                        m = cmds[(dname, func, p in blades)]
                        c = m.get(p, m.get("default"))
                        if not c:
                            continue
                        missing = [st for st in c.steps if st not in BUILDERS]
                        if missing or len(c.steps) != 1:
                            skipped[(key, ",".join(c.steps))] += 1
                            continue
                        per_pid[p][key] = c
            common_feats = set.intersection(*pid_feats.values())
            common_proto = {k: c for k, c in per_pid[pids[0]].items() if all(per_pid[p].get(k) == c for p in pids)}

            def enc(c: Command) -> dict:
                return {"fn": c.fn, "txn": c.txn, "steps": list(c.steps), "args": list(c.args)}

            pid_protocol, pid_features = {}, {}
            for p in pids:
                extra = {k: enc(c) for k, c in sorted(per_pid[p].items()) if k not in common_proto}
                if extra:
                    pid_protocol[f"0x{p:04X}"] = extra
                if pid_feats[p] - common_feats:
                    pid_features[f"0x{p:04X}"] = sorted(pid_feats[p] - common_feats)
            for p in pids:
                for c in per_pid[p].values():
                    fn_counts[(ci.kind, c.fn)] += 1
            has_static = any("set_effect:matrix:static" in per_pid[p] for p in pids)
            has_bright = any(f == "matrix_brightness" for p in pids for f in pid_files[p])
            zones = {"matrix": ["static"] if has_static else []} if has_static or has_bright else {}
            kinds[ci.kind] += 1
            devices.append({
                "model": slug, "name": ci.name, "kind": ci.kind,
                "pids": {f"0x{p:04X}": ci.pids[p] for p in pids},
                "dpi_max": 0, "poll_rates": [],
                "features": sorted(common_feats), "pid_features": pid_features,
                "zones": zones,
                "protocol": {k: enc(c) for k, c in sorted(common_proto.items())},
                "pid_protocol": pid_protocol,
                "interface": 0, "verified": False,
            })

    print("models per kind: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())), file=stderr)
    print("commands per kind/builder (device PIDs):", file=stderr)
    for (kind, fn), n in sorted(fn_counts.items()):
        print(f"  {kind} {fn} x{n}", file=stderr)
    for (key, missing), n in sorted(skipped.items()):
        print(f"skipped {key} (steps not a single ported builder: {missing}) x{n} PIDs", file=stderr)
    print(f"{len(devices)} models, {sum(len(d['pids']) for d in devices)} PIDs", file=stderr)

    commit = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return {
        "_source": {"repo": REPO_URL, "commit": commit, "license": "GPL-2.0-only (OpenRazer)"},
        "devices": sorted(devices, key=lambda d: d["model"]),
    }


UDEV_UINPUT = 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"'


def udev_rules(db: dict) -> str:
    """udev rules (hidraw + input per vendor 1532, one line each) for every PID in the DB."""
    pids = set()
    for d in db["devices"]:
        pids |= {int(p, 16) for p in d["pids"]} | {int(p, 16) for p in d.get("pid_protocol", {})}
    ids = "|".join(f"{p:04x}" for p in sorted(pids))
    return (
        "# RazerTux: give the logged-in user access to supported Razer mice and /dev/uinput\n"
        "# Generated by tools/import_openrazer.py --udev from razertux/devices/openrazer_mice.json; do not edit.\n"
        f'SUBSYSTEM=="hidraw", ATTRS{{idVendor}}=="1532", ATTRS{{idProduct}}=="{ids}", TAG+="uaccess"\n'
        f'SUBSYSTEM=="input", ATTRS{{idVendor}}=="1532", ATTRS{{idProduct}}=="{ids}", TAG+="uaccess"\n'
        f"{UDEV_UINPUT}\n"
    )


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--udev"]:
        db_path = Path(__file__).resolve().parent.parent / "razertux" / "devices" / "openrazer_mice.json"
        sys.stdout.write(udev_rules(json.loads(db_path.read_text())))
        return 0
    if len(argv) == 2 and argv[1] == "--others":
        sys.stdout.write(json.dumps(build_other_db(Path(argv[0])), indent=1, sort_keys=True) + "\n")
        return 0
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    db = build_db(Path(argv[0]))
    sys.stdout.write(json.dumps(db, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
