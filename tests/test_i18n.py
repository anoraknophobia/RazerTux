"""i18n: language resolution, plain-Python tr(), translated daemon/store texts and
the completeness of the JSON catalogs (every tr() string of the package)."""
import ast
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from razertux import i18n
from razertux.config import ConfigStore, DisabledAction, Settings, default_profile, validate
from razertux.hw.buttons import ButtonId

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "razertux"
LANGS = ("de", "es", "fr")
# self.tr() in a mixin runs with the context of the class it is mixed into
MIXIN_CONTEXT = {"ProfileActions": "MainWindow"}


# -- extraction ------------------------------------------------------------------
def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _wrapper_contexts(tree):
    """Module-level helpers like `def _tr(text): return QCoreApplication.translate("ctx", text)`."""
    out = {}
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef):
            for call in ast.walk(fn):
                if (isinstance(call, ast.Call) and len(call.args) >= 2 and _const(call.args[0])
                        and _callee(call) in ("translate", "tr")
                        and isinstance(call.args[1], ast.Name)):
                    out[fn.name] = _const(call.args[0])
    return out


def _callee(call):
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None


class _Extractor(ast.NodeVisitor):
    def __init__(self, path, wrappers):
        self.path, self.wrappers = path, wrappers
        self.classes: list[str] = []
        self.found: set[tuple[str, str]] = set()   # (context, source)
        self.noops: set[tuple[str, str]] = set()
        self.dynamic: list[tuple[str, int]] = []   # (context, line) of tr(<variable>)
        self.bad: list[str] = []

    def visit_ClassDef(self, node):
        self.classes.append(MIXIN_CONTEXT.get(node.name, node.name))
        self.generic_visit(node)
        self.classes.pop()

    def _add(self, ctx, arg, node):
        text = _const(arg)
        if text is not None:
            self.found.add((ctx, text))
        elif isinstance(arg, (ast.JoinedStr, ast.BinOp)):
            self.bad.append(f"{self.path.name}:{node.lineno}: tr() of a built string")
        else:
            self.dynamic.append((ctx, node.lineno))

    def visit_Call(self, node):
        name, f = _callee(node), node.func
        if (name == "tr" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "self" and node.args):
            self._add(self.classes[-1], node.args[0], node)
        elif name in ("translate", "QT_TRANSLATE_NOOP", "tr") and len(node.args) >= 2 \
                and _const(node.args[0]) is not None:
            ctx = _const(node.args[0])
            if name == "QT_TRANSLATE_NOOP":
                self.noops.add((ctx, _const(node.args[1])))
            if not (isinstance(node.args[1], ast.Name) and self._inside_wrapper(node)):
                self._add(ctx, node.args[1], node)
        elif isinstance(f, ast.Name) and f.id in self.wrappers and node.args:
            self._add(self.wrappers[f.id], node.args[0], node)
        self.generic_visit(node)

    def _inside_wrapper(self, node):
        return any(fn.lineno <= node.lineno <= fn.end_lineno for fn in self._wrapper_defs)


def extract():
    found, noops, dynamic, bad = set(), set(), [], []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        ex = _Extractor(path, _wrapper_contexts(tree))
        ex._wrapper_defs = [fn for fn in tree.body if isinstance(fn, ast.FunctionDef)
                            and fn.name in ex.wrappers]
        ex.visit(tree)
        found |= ex.found
        noops |= ex.noops
        dynamic += [(ctx, f"{path.name}:{line}") for ctx, line in ex.dynamic]
        bad += ex.bad
    return found, noops, dynamic, bad


def catalog_pairs(lang):
    data = json.loads((PKG / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
    return {(ctx, src): dst for ctx, entries in data.items() for src, dst in entries.items()}


_PLACEHOLDER = re.compile(r"%\d+|%n|\{\w+\}")


def test_extractor_sees_all_kinds_of_calls():
    found, noops, dynamic, _ = extract()
    assert ("MainWindow", "CUSTOMIZE") in found                  # self.tr in a class
    assert ("MainWindow", "New profile") in found                # mixin -> MainWindow
    assert ("keynames", "Key %1") in found                       # module _tr wrapper
    assert ("ActionDialog", "Key combination") in noops          # QT_TRANSLATE_NOOP table
    assert ("validate", "Left click unreachable") in found       # plain-Python i18n.tr
    assert ("daemon", "Naga V2 Pro: Low battery") in found
    assert any(ctx == "LightingTab" for ctx, _ in dynamic)


def test_no_tr_of_built_strings():
    assert extract()[3] == []


def test_dynamic_tr_calls_are_backed_by_noop_tables():
    _, noops, dynamic, _ = extract()
    noop_contexts = {ctx for ctx, _ in noops}
    missing = [(ctx, where) for ctx, where in dynamic if ctx not in noop_contexts]
    assert missing == []


@pytest.mark.parametrize("lang", LANGS)
def test_catalog_is_complete_without_stale_entries(lang):
    found = extract()[0]
    cat = catalog_pairs(lang)
    assert sorted(found - cat.keys()) == [], "untranslated"
    assert sorted(cat.keys() - found) == [], "stale"
    assert [k for k, v in cat.items() if not v.strip()] == []


@pytest.mark.parametrize("lang", LANGS)
def test_placeholders_match(lang):
    wrong = [(k, v) for k, v in catalog_pairs(lang).items()
             if Counter(_PLACEHOLDER.findall(k[1])) != Counter(_PLACEHOLDER.findall(v))]
    assert wrong == []


def test_english_is_the_source_and_needs_no_catalog():
    assert i18n.SOURCE_LANGUAGE == "en"
    assert i18n.catalog("en") == {}
    assert not (PKG / "i18n" / "en.json").exists()


def test_german_is_a_catalog_like_the_others():
    assert i18n.lookup("de", "MainWindow", "CUSTOMIZE") == "TASTEN"
    assert i18n.lookup("de", "validate", "Left click unreachable") == "Linksklick nicht erreichbar"


# -- language resolution ---------------------------------------------------------
def test_resolve_language_explicit_and_system():
    assert i18n.resolve_language("fr") == "fr"
    assert i18n.resolve_language("system", "es_ES") == "es"
    assert i18n.resolve_language("system", "it_IT") == "en"   # unsupported -> English
    assert i18n.resolve_language("klingon", "de_DE") == "de"  # unknown setting = system
    assert i18n.resolve_language("system", "") == "en"
    assert i18n.resolve_language("system", "de_AT") == "de"   # German locale -> German catalog
    assert i18n.resolve_language("system", "en_GB") == "en"
    for lang in ("de", "en", "es", "fr"):
        assert i18n.resolve_language(lang, "it_IT") == lang


def test_unknown_language_falls_back_to_the_english_source():
    i18n.set_language("klingon")
    assert i18n.current_language() == "en"
    assert i18n.tr("validate", "Left click unreachable") == "Left click unreachable"


def test_system_language_from_environment(monkeypatch):
    assert i18n.system_language() == "en"
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert i18n.system_language() == "de"
    assert i18n.resolve_language("system") == "de"
    monkeypatch.setenv("LANGUAGE", "fr:en")
    assert i18n.system_language() == "fr"
    monkeypatch.delenv("LANGUAGE")
    monkeypatch.setenv("LC_ALL", "C")
    assert i18n.resolve_language("system") == "en"


def test_tr_formats_and_translates():
    assert i18n.tr("validate", "Macro missing: {macro}", macro="x") == "Macro missing: x"
    i18n.set_language("de")
    assert i18n.current_language() == "de"
    assert i18n.tr("validate", "Macro missing: {macro}", macro="x") == "Makro fehlt: x"
    assert i18n.tr("nope", "Unknown {a}", a=1) == "Unknown 1"  # falls back to source


def test_apply_settings_sets_language():
    assert i18n.apply_settings(Settings("default", language="es")) == "es"
    assert i18n.current_language() == "es"


def test_gui_help_follows_the_language(capsys):
    from razertux.gui.app import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--help"])
    assert "start only the tray icon (window hidden)" in capsys.readouterr().out
    i18n.set_language("de")
    with pytest.raises(SystemExit):
        parse_args(["--help"])
    assert "nur Tray-Symbol starten (Fenster versteckt)" in capsys.readouterr().out


# -- settings --------------------------------------------------------------------
def test_settings_language_round_trip():
    assert Settings("default").language == "system"
    s = Settings.from_dict(Settings("p", language="fr").to_dict())
    assert s.language == "fr"
    assert Settings.from_dict({"active_profile": "p"}).language == "system"


# -- translated daemon / store texts -----------------------------------------------
def test_validation_and_store_errors_follow_the_language(tmp_path):
    p = default_profile()
    p.standard[ButtonId.LEFT] = DisabledAction()
    assert validate(p) == ["Left click unreachable"]
    i18n.set_language("de")
    assert validate(p) == ["Linksklick nicht erreichbar"]
    (tmp_path / "macros.json").write_text("{kaputt")
    store = ConfigStore(tmp_path)
    store.load_macros()
    assert store.errors and store.errors[0].startswith("macros.json ist beschädigt")


async def test_service_reload_switches_daemon_language(tmp_path):
    from test_dbus_service import call, make
    d, svc, store, _bus = make(tmp_path)
    store.save_settings(Settings("default", language="fr"))
    (tmp_path / "macros.json").write_text("{kaputt")
    await call(svc, "Reload")
    assert i18n.current_language() == "fr"
    errors = (await call(svc, "GetStatus", "naga_v2_pro"))["config_errors"].value
    assert errors and "macros.json" in errors[0] and "corrupt" not in errors[0]


async def test_low_battery_notification_is_translated(tmp_path):
    from test_daemon_devices import connected
    d, t = await connected(tmp_path, product=0x00A8, settings=Settings("default", language="en"))
    await d.service.reload()
    sent = []
    d.notify = lambda title, body: sent.append((title, body))
    t.responses[(0x07, 0x80)] = bytes([0, 25])  # ~10 %
    t.responses[(0x07, 0x84)] = bytes([0, 0])
    d.sessions["naga_v2_pro"]._battery.poll()
    assert sent == [("Naga V2 Pro: Low battery", "Battery at 10 % – please charge the mouse.")]
    await d.shutdown()


async def test_low_battery_notification_in_german(tmp_path):
    from test_daemon_devices import connected
    d, t = await connected(tmp_path, product=0x00A8, settings=Settings("default", language="de"))
    await d.service.reload()
    sent = []
    d.notify = lambda title, body: sent.append((title, body))
    t.responses[(0x07, 0x80)] = bytes([0, 25])  # ~10 %
    t.responses[(0x07, 0x84)] = bytes([0, 0])
    d.sessions["naga_v2_pro"]._battery.poll()
    assert sent == [("Naga V2 Pro: Akku schwach", "Akkustand 10 % – bitte die Maus laden.")]
    await d.shutdown()


# -- command line texts ------------------------------------------------------------
def _german_config(home):
    ConfigStore(home / ".config/razertux").save_settings(Settings("default", language="de"))


def test_cli_prints_german_with_language_de(monkeypatch, fake_home, capsys):
    from razertux import cli
    _german_config(fake_home)
    monkeypatch.setattr(cli, "_dbus_restore", lambda model="naga_v2_pro": [])
    assert cli.main(["restore"]) == 0
    assert capsys.readouterr().out == "Wiederhergestellt.\n"
    monkeypatch.setattr(cli, "_dbus_restore", lambda model="naga_v2_pro": ["dpi"])
    assert cli.main(["restore"]) == 2
    assert capsys.readouterr().err == "Nicht wiederhergestellt: dpi\n"
    assert cli.main(["status", "--model", "nope"]) == 1
    assert capsys.readouterr().err.startswith("Fehler: Unbekanntes Modell: nope")


def test_cli_and_daemon_help_follow_the_language(fake_home, capsys):
    from razertux import cli
    from razertux.daemon import main as daemon_main
    _german_config(fake_home)
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "Mauszustand als JSON ausgeben" in out and "Mauszustand einmalig sichern" in out
    with pytest.raises(SystemExit):
        daemon_main.main(["--help"])
    assert "ausführliches Protokoll" in capsys.readouterr().out


def test_cli_stays_english_with_language_en(monkeypatch, fake_home, capsys):
    from razertux import cli
    monkeypatch.setattr(cli, "_dbus_restore", lambda model="naga_v2_pro": [])
    assert cli.main(["restore"]) == 0
    assert capsys.readouterr().out == "Restored.\n"
