import re
import subprocess
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
SWITCH_CMD = ("systemctl --user disable --now naga-configd.service 2>/dev/null; "
              "systemctl --user enable --now razertuxd.service")


def _read(name):
    return (PKG / name).read_text()


def test_old_packaging_files_are_gone():
    for old in ("naga-config.desktop", "naga-config-tray.desktop", "naga-configd.service",
                "io.github.nagaconfig.service", "70-naga-config.rules"):
        assert not (PKG / old).exists(), old
    assert not (ROOT / "naga_config").exists()


def test_desktop_entry():
    lines = _read("razertux.desktop").splitlines()
    for line in ("Name=RazerTux", "GenericName=Razer device configuration",
                 "GenericName[de]=Razer-Gerätekonfiguration",
                 "Comment=Unofficial configuration for Razer mice",
                 "Comment[de]=Inoffizielle Konfiguration für Razer-Mäuse", "Exec=razertux-gui",
                 "Icon=razertux", "Categories=Settings;HardwareSettings;Qt;"):
        assert line in lines


def test_autostart_entry():
    lines = _read("razertux-tray.desktop").splitlines()
    assert "Exec=razertux-gui --tray" in lines
    assert "Icon=razertux" in lines
    assert "X-KDE-autostart-after=panel" in lines
    assert any(line.startswith("Name=RazerTux") for line in lines)


def test_dbus_activation_and_unit():
    s = _read("io.github.razertux.service")
    assert "Name=io.github.razertux" in s.splitlines()
    assert "SystemdService=razertuxd.service" in s.splitlines()
    u = _read("razertuxd.service")
    assert "Type=dbus" in u and "BusName=io.github.razertux" in u.splitlines()
    assert "ExecStart=/usr/bin/razertuxd" in u.splitlines()
    assert "Documentation=file:///usr/share/doc/razertux/README.md" in u.splitlines()


def test_unit_starts_with_the_graphical_session():
    lines = _read("razertuxd.service").splitlines()
    for line in ("After=graphical-session.target", "PartOf=graphical-session.target",
                 "WantedBy=graphical-session.target", "Restart=on-failure", "RestartSec=2"):
        assert line in lines
    assert "WantedBy=default.target" not in lines


def _import_tool():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("import_openrazer", ROOT / "tools" / "import_openrazer.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["import_openrazer"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_udev_rules():
    from razertux.devices.db import load_devices
    r = _read("70-razertux.rules")
    lines = r.splitlines()
    assert lines[0].startswith("# RazerTux:") and "generated" in lines[1].lower()
    rule = [l for l in lines if not l.startswith("#")]
    assert len(rule) == 3
    pids = set()
    for d in load_devices().values():
        pids |= set(d.pids) | set(d.pid_protocol)
    assert len(pids) > 100
    for sub, line in zip(("hidraw", "input"), rule[:2]):
        assert line.startswith(f'SUBSYSTEM=="{sub}", ATTRS{{idVendor}}=="1532", ATTRS{{idProduct}}=="')
        assert line.endswith('", TAG+="uaccess"')
        listed = line.split('ATTRS{idProduct}=="')[1].split('"')[0].split("|")
        assert listed == sorted(listed) and all(re.fullmatch(r"[0-9a-f]{4}", x) for x in listed)
        assert {int(x, 16) for x in listed} == pids
        assert {"00a7", "00a8"} <= set(listed)
    assert rule[2] == 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"'


def test_udev_rules_are_generated_from_the_database():
    tool = _import_tool()
    assert tool.udev_rules(tool.json.loads(
        (ROOT / "razertux" / "devices" / "openrazer_mice.json").read_text())) == _read("70-razertux.rules")


def test_openrazer_notice_is_packaged():
    n = _read("OPENRAZER-NOTICE")
    assert "OpenRazer" in n and "GPL-2.0" in n and "openrazer_mice.json" in n
    p = _read("PKGBUILD")
    assert '"$p/OPENRAZER-NOTICE" "$pkgdir/usr/share/licenses/$pkgname/OPENRAZER-NOTICE"' in p
    assert "OPENRAZER-NOTICE" in (ROOT / "README.md").read_text()


def test_readme_documents_new_sections():
    r = (ROOT / "README.md").read_text()
    for h in ("## Supported mice", "## Laser graphics", "## On-screen display (OSD)"):
        assert h in r, h
    assert "Copy diagnostics" in r
    assert r.startswith("# RazerTux")


def test_hardware_notes_list_unverified_assumptions():
    h = (ROOT / "docs" / "hardware-notes.md").read_text()
    assert "Plates 6/2" in h and "generic driver" in h


def test_pkgbuild_names_and_relations():
    p = _read("PKGBUILD")
    lines = p.splitlines()
    for line in ("pkgname=razertux", "pkgver=0.6.0", "pkgrel=1", "replaces=(naga-config)",
                 "conflicts=(naga-config openrazer-daemon openrazer-driver-dkms)",
                 "provides=()", "install=razertux.install"):
        assert line in lines, line
    assert "depends=(python python-evdev pyside6 python-pyudev python-dbus-next polkit)" in p
    assert "license=('GPL-2.0-or-later')" in p and "arch=('any')" in p
    assert [l for l in lines if l.startswith("url=")] == ["url='https://github.com/anoraknophobia/RazerTux'"]
    rest = "\n".join(l for l in lines if not l.startswith(("replaces=", "conflicts=")))
    for old in ("naga-config", "naga_config", "nagaconfig"):
        assert old not in rest


def test_pkgbuild_installs_all_files():
    p = _read("PKGBUILD")
    for src, dest in (("70-razertux.rules", "usr/lib/udev/rules.d/70-razertux.rules"),
                      ("razertuxd.service", "usr/lib/systemd/user/razertuxd.service"),
                      ("io.github.razertux.service", "usr/share/dbus-1/services/io.github.razertux.service"),
                      ("razertux.desktop", "usr/share/applications/razertux.desktop"),
                      ("razertux-tray.desktop", "etc/xdg/autostart/razertux-tray.desktop"),
                      ("razertux.svg", "usr/share/icons/hicolor/scalable/apps/razertux.svg")):
        assert (PKG / src).is_file(), src
        assert f'"$p/{src}" "$pkgdir/{dest}"' in p, dest
    assert 'cp -r "$startdir/../razertux"' in p


def test_install_script_only_prints_the_switch_commands():
    s = _read("razertux.install")
    assert "post_install()" in s and "post_upgrade()" in s
    assert SWITCH_CMD in s
    # pacman runs this as root: printing only, never managing user services itself
    code = "\n".join(line for line in s.splitlines()
                     if "echo" not in line and not line.lstrip().startswith("#"))
    assert "systemctl" not in code
    def run(call):
        return subprocess.run(["bash", "-c", f"source {PKG / 'razertux.install'}; {call}"],
                              capture_output=True, text=True, check=True).stdout
    assert run("post_install 0.2.0-1").count(SWITCH_CMD) == 1
    assert run("post_upgrade 0.2.0-1 0.1.0-1").count(SWITCH_CMD) == 1  # from the naga-config era
    assert "restart razertuxd" not in run("post_install 0.3.0-1")
    # from < 0.3.0 the running daemon and tray are old -> restart hint
    for old in ("0.2.0-1", "0.2.9-1"):
        out = run(f"post_upgrade 0.3.0-1 {old}")
        assert "systemctl --user restart razertuxd.service" in out
        assert "tray" in out and "log out" in out and "anmelden" not in out
        assert SWITCH_CMD not in out
    both = run("post_upgrade 0.3.0-1 0.1.0-1")  # naga-config era: switch hint, then enable
    assert both.count(SWITCH_CMD) == 1 and "restart razertuxd.service" not in both
    # 0.3.x -> 0.4.0: the daemon gained PauseDevice/ResumeDevice -> restart hint
    out = run("post_upgrade 0.4.0-1 0.3.0-1")
    assert "systemctl --user restart razertuxd.service" in out and SWITCH_CMD not in out
    assert run("post_upgrade 0.4.1-1 0.4.0-1") == ""                    # later upgrades stay quiet


def test_pkgbuild_is_valid_bash():
    subprocess.run(["bash", "-n", str(PKG / "PKGBUILD")], check=True)
    subprocess.run(["bash", "-n", str(PKG / "razertux.install")], check=True)


def test_icon_is_a_scalable_svg_with_the_green_accent():
    svg = (PKG / "razertux.svg").read_text()
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg") and root.get("viewBox")
    assert "#44D62C" in svg.upper()
    # the same icon ships inside the package for the window and tray icon
    assert (ROOT / "razertux" / "gui" / "assets" / "razertux.svg").read_text() == svg


def test_entry_points_match_packaging():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["name"] == "razertux"
    assert data["project"]["version"] == "0.6.0"
    scripts = data["project"]["scripts"]
    assert scripts == {"razertux": "razertux.cli:main", "razertuxd": "razertux.daemon.main:main",
                       "razertux-gui": "razertux.gui.app:main",
                       "razertux-record": "razertux.gui.recorder_wizard:main"}
    assert "ExecStart=/usr/bin/razertuxd" in _read("razertuxd.service")
    assert "Exec=/usr/bin/razertuxd" in _read("io.github.razertux.service")
    for f in ("razertux.desktop", "razertux-tray.desktop"):
        m = re.search(r"^Exec=(\S+)", _read(f), re.M)
        assert m.group(1) in scripts


def test_package_version_matches():
    import razertux
    assert razertux.__version__ == "0.6.0"


def test_pyproject_license_is_spdx_expression():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["license"] == "GPL-2.0-or-later"  # PEP 639 string, no table
    # SPDX license strings need setuptools >= 77 (older versions warn or fail)
    assert "setuptools>=77" in data["build-system"]["requires"]


def test_readme_has_no_manual_backup_step():
    readme = (ROOT / "README.md").read_text()
    steps = readme.split("## Getting started")[1].split("## ")[0]
    assert not re.search(r"^\d+\..*razertux backup", steps, re.M)  # no numbered step
    assert "automatically" in steps and "optional" in steps


def test_readme_names_disclaimer_without_old_name():
    readme = (ROOT / "README.md").read_text()
    assert readme.startswith("# RazerTux")
    head = readme[:800]
    assert "Not an official Razer product; not affiliated with Razer Inc." in head
    assert "OpenRazer" in readme and "GPL" in readme
    assert "https://github.com/anoraknophobia/RazerTux" in head
    # the former project name is not mentioned on GitHub
    for old in ("naga-config", "naga_config", "nagaconfig", "formerly"):
        assert old not in readme

def test_translations_and_icon_are_packaged():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["tool"]["setuptools"]["packages"]["find"]["include"] == ["razertux*"]
    pd = data["tool"]["setuptools"]["package-data"]
    assert pd["razertux.i18n"] == ["*.json"]
    assert pd["razertux.gui"] == ["assets/*.svg", "assets/layouts/*.json"]
    for lang in ("de", "es", "fr"):
        assert (ROOT / "razertux" / "i18n" / f"{lang}.json").is_file()


def test_build_artifacts_are_ignored():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    for pattern in ("packaging/src/", "packaging/pkg/", "*.pkg.tar.zst"):
        assert pattern in ignored


def test_descriptions_name_all_razer_mice_like_the_pkgbuild():
    """Cheap fix: no description claims the Naga V2 Pro is the only mouse."""
    import razertux
    from razertux.daemon.main import build_parser
    pkgdesc = re.search(r"pkgdesc='([^']*)'", _read("PKGBUILD")).group(1)
    assert "other Razer mice" in pkgdesc
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert "Razer Naga V2 Pro and other Razer mice" in project["description"]
    assert "(Razer Naga V2 Pro and other Razer mice)" in " ".join(razertux.__doc__.split())
    assert build_parser().description == "RazerTux service (Razer mice)"


def test_readme_wording_and_line_width():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "no guarantee" in readme
    assert all(len(line) <= 100 for line in readme.splitlines())


def test_record_helper_wrapper_and_policy():
    w = _read("razertux-record-helper").splitlines()
    assert w[0] == "#!/bin/sh"
    assert 'exec /usr/bin/python3 -I -m razertux.recorder.helper "$@"' in w
    p = _read("PKGBUILD")
    assert ('install -Dm755 "$p/razertux-record-helper" '
            '"$pkgdir/usr/lib/razertux/razertux-record-helper"') in p
    assert ('install -Dm644 "$p/io.github.razertux.record.policy" '
            '"$pkgdir/usr/share/polkit-1/actions/io.github.razertux.record.policy"') in p
    from razertux.recorder.backend import HELPER_PATH
    assert HELPER_PATH == "/usr/lib/razertux/razertux-record-helper"
    root = ET.fromstring(_read("io.github.razertux.record.policy"))
    ann = root.find("action/annotate[@key='org.freedesktop.policykit.exec.path']")
    assert ann.text == HELPER_PATH


def test_readme_help_add_your_device():
    r = (ROOT / "README.md").read_text(encoding="utf-8")
    sec = r.split("## Help add your device")[1].split("\n## ")[0]
    assert len(re.findall(r"^\d\. ", sec, re.M)) == 6
    for needle in ("Record device", "razertux-record", "GitHub account", "NOSTORE",
                   "serial", "tools/import_device_report.py", "For maintainers"):
        assert needle in sec, needle


def test_readme_names_the_record_menu_entry_as_the_gui_does():
    readme = (ROOT / "README.md").read_text()
    gui = (ROOT / "razertux" / "gui" / "main_window.py").read_text()
    assert 'self.tr("Record device…")' in gui
    assert '"Record device…"' in readme and '"Record device..."' not in readme


def test_changelog_has_notes_for_the_current_version():
    import importlib.util

    import razertux
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("release_notes", root / "tools/release_notes.py")
    rn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rn)
    notes = rn.sections((root / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert next(iter(notes)) == razertux.__version__  # newest section first = this version
    assert notes[razertux.__version__].strip()
    assert rn.main(["v" + razertux.__version__]) == 0 and rn.main(["9.9.9"]) == 1
    wf = (root / ".github/workflows/release.yml").read_text()
    assert "tools/release_notes.py" in wf and "tags:" in wf


def test_release_notes_join_wrapped_lines():
    import importlib.util
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("release_notes", root / "tools/release_notes.py")
    rn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rn)
    notes = rn.sections("## [1.0.0] - 2026-01-01\n\n### Fixed\n- one\n  two\n- three\n")
    assert notes == {"1.0.0": "### Fixed\n- one two\n- three\n"}


def test_install_script_is_valid_bash_and_matches_the_pkgbuild():
    root = Path(__file__).resolve().parent.parent
    script = root / "install.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert script.stat().st_mode & 0o111  # executable
    out = subprocess.run([str(script), "--print-files"], check=True, capture_output=True, text=True).stdout
    names = {Path(line).name for line in out.split()}
    pkgbuild = (root / "packaging/PKGBUILD").read_text()
    system = set(re.findall(r'install -Dm\d+ "\$p/([^"]+)"', pkgbuild)) - {"OPENRAZER-NOTICE"}
    assert system <= names  # every system file of the package is installed by the script too
    assert {"razertux", "razertuxd", "razertux-gui", "razertux-record"} <= names


def test_install_script_rejects_unknown_options():
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([str(root / "install.sh"), "--bogus"], capture_output=True, text=True)
    assert r.returncode == 2 and "--uninstall" in r.stdout
