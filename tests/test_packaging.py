import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"


def _read(name):
    return (PKG / name).read_text()


def test_desktop_entry():
    t = _read("naga-config.desktop")
    for line in ("Name=Naga Config", "GenericName=Mauskonfiguration", "Exec=naga-config-gui",
                 "Icon=input-mouse", "Categories=Settings;HardwareSettings;Qt;"):
        assert line in t.splitlines()


def test_autostart_entry():
    t = _read("naga-config-tray.desktop")
    assert "Exec=naga-config-gui --tray" in t.splitlines()
    assert "X-KDE-autostart-after=panel" in t.splitlines()


def test_dbus_activation_and_unit():
    s = _read("io.github.nagaconfig.service")
    assert "Name=io.github.nagaconfig" in s
    assert "SystemdService=naga-configd.service" in s
    u = _read("naga-configd.service")
    assert "Type=dbus" in u and "BusName=io.github.nagaconfig" in u
    assert "ExecStart=/usr/bin/naga-configd" in u


def test_unit_starts_with_the_graphical_session():
    lines = _read("naga-configd.service").splitlines()
    for line in ("After=graphical-session.target", "PartOf=graphical-session.target",
                 "WantedBy=graphical-session.target", "Restart=on-failure", "RestartSec=2"):
        assert line in lines
    assert "WantedBy=default.target" not in lines


def test_udev_rules():
    r = _read("70-naga-config.rules")
    assert 'idVendor}=="1532"' in r and "00a7|00a8" in r and "uinput" in r


def test_pkgbuild_installs_all_files():
    p = _read("PKGBUILD")
    for dest in ("usr/lib/udev/rules.d/70-naga-config.rules",
                 "usr/lib/systemd/user/naga-configd.service",
                 "usr/share/dbus-1/services/io.github.nagaconfig.service",
                 "usr/share/applications/naga-config.desktop",
                 "etc/xdg/autostart/naga-config-tray.desktop"):
        assert dest in p
    for src in ("70-naga-config.rules", "naga-configd.service", "io.github.nagaconfig.service",
                "naga-config.desktop", "naga-config-tray.desktop"):
        assert (PKG / src).is_file()
    assert "conflicts=(openrazer-daemon openrazer-driver-dkms)" in p
    assert "depends=(python python-evdev pyside6 python-pyudev python-dbus-next)" in p
    assert "license=('GPL-2.0-or-later')" in p and "arch=('any')" in p


def test_entry_points_match_packaging():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert set(scripts) == {"naga-config", "naga-configd", "naga-config-gui"}
    assert "ExecStart=/usr/bin/naga-configd" in _read("naga-configd.service")
    assert "Exec=/usr/bin/naga-configd" in _read("io.github.nagaconfig.service")
    for f in ("naga-config.desktop", "naga-config-tray.desktop"):
        m = re.search(r"^Exec=(\S+)", _read(f), re.M)
        assert m.group(1) in scripts


def test_pyproject_license_is_spdx_expression():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["license"] == "GPL-2.0-or-later"  # PEP 639 string, no table
    # SPDX license strings need setuptools >= 77 (older versions warn or fail)
    assert "setuptools>=77" in data["build-system"]["requires"]


def test_readme_has_no_manual_backup_step():
    readme = (ROOT / "README.md").read_text()
    steps = readme.split("## Erste Schritte")[1].split("## ")[0]
    assert not re.search(r"^\d+\..*naga-config backup", steps, re.M)  # no numbered step
    assert "automatisch" in steps and "optional" in steps
    assert "lässt die Maus im Treibermodus, bis sie neu angesteckt wird" not in readme


def test_translations_are_packaged():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["tool"]["setuptools"]["package-data"]["razertux.i18n"] == ["*.json"]
    for lang in ("en", "es", "fr"):
        assert (ROOT / "razertux" / "i18n" / f"{lang}.json").is_file()
    # the PKGBUILD copies the whole package directory, catalogs included
    assert 'cp -r "$startdir/../razertux"' in _read("PKGBUILD")
