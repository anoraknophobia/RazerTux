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
    for line in ("Name=RazerTux", "GenericName=Razer-Gerätekonfiguration",
                 "Comment=Inoffizielle Konfiguration für Razer-Mäuse", "Exec=razertux-gui",
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


def test_udev_rules():
    r = _read("70-razertux.rules")
    assert 'idVendor}=="1532"' in r and "00a7|00a8" in r and "uinput" in r
    assert r.splitlines()[0].startswith("# RazerTux:")


def test_pkgbuild_names_and_relations():
    p = _read("PKGBUILD")
    lines = p.splitlines()
    for line in ("pkgname=razertux", "pkgver=0.2.0", "pkgrel=1", "replaces=(naga-config)",
                 "conflicts=(naga-config openrazer-daemon openrazer-driver-dkms)",
                 "provides=()", "install=razertux.install"):
        assert line in lines, line
    assert "depends=(python python-evdev pyside6 python-pyudev python-dbus-next)" in p
    assert "license=('GPL-2.0-or-later')" in p and "arch=('any')" in p
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
    out = subprocess.run(["bash", "-c", f"source {PKG / 'razertux.install'}; post_install; post_upgrade"],
                         capture_output=True, text=True, check=True).stdout
    assert out.count(SWITCH_CMD) == 2


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
    assert data["project"]["version"] == "0.2.0"
    scripts = data["project"]["scripts"]
    assert scripts == {"razertux": "razertux.cli:main", "razertuxd": "razertux.daemon.main:main",
                       "razertux-gui": "razertux.gui.app:main"}
    assert "ExecStart=/usr/bin/razertuxd" in _read("razertuxd.service")
    assert "Exec=/usr/bin/razertuxd" in _read("io.github.razertux.service")
    for f in ("razertux.desktop", "razertux-tray.desktop"):
        m = re.search(r"^Exec=(\S+)", _read(f), re.M)
        assert m.group(1) in scripts


def test_package_version_matches():
    import razertux
    assert razertux.__version__ == "0.2.0"


def test_pyproject_license_is_spdx_expression():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["license"] == "GPL-2.0-or-later"  # PEP 639 string, no table
    # SPDX license strings need setuptools >= 77 (older versions warn or fail)
    assert "setuptools>=77" in data["build-system"]["requires"]


def test_readme_has_no_manual_backup_step():
    readme = (ROOT / "README.md").read_text()
    steps = readme.split("## Erste Schritte")[1].split("## ")[0]
    assert not re.search(r"^\d+\..*razertux backup", steps, re.M)  # no numbered step
    assert "automatisch" in steps and "optional" in steps
    assert "lässt die Maus im Treibermodus, bis sie neu angesteckt wird" not in readme


def test_readme_names_disclaimer_and_switch_over():
    readme = (ROOT / "README.md").read_text()
    assert readme.startswith("# RazerTux")
    head = readme[:800]
    assert "Kein offizielles Razer-Produkt, nicht mit Razer Inc. verbunden." in head
    assert "Not an official Razer product; not affiliated with Razer Inc." in head
    switch = readme.split("## Umstieg von naga-config")[1].split("\n## ")[0]
    assert "systemctl --user disable --now naga-configd.service" in switch
    assert "systemctl --user enable --now razertuxd.service" in switch
    assert "~/.config/razertux" in switch
    assert "OpenRazer" in readme and "GPL" in readme
    # the old name only appears where the switch-over is explained
    rest = readme.replace(switch, "")
    assert "naga-configd" not in rest and "~/.config/naga-config" not in rest


def test_translations_and_icon_are_packaged():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["tool"]["setuptools"]["packages"]["find"]["include"] == ["razertux*"]
    pd = data["tool"]["setuptools"]["package-data"]
    assert pd["razertux.i18n"] == ["*.json"]
    assert pd["razertux.gui"] == ["assets/*.svg"]
    for lang in ("en", "es", "fr"):
        assert (ROOT / "razertux" / "i18n" / f"{lang}.json").is_file()


def test_build_artifacts_are_ignored():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    for pattern in ("packaging/src/", "packaging/pkg/", "*.pkg.tar.zst"):
        assert pattern in ignored
