import pytest
from fake_daemon import DA, NAGA, FakeTransport
from PySide6.QtGui import QColor

from razertux.gui.battery_icon import (
    GREEN, ORANGE, RED, SIZES, BatteryTray, battery_colour, battery_icon, battery_tooltip,
    paint_battery,
)
from razertux.gui.dbus_client import DaemonClient


@pytest.mark.parametrize("level,colour", [(100, GREEN), (21, GREEN), (20, ORANGE),
                                          (11, ORANGE), (10, RED), (0, RED)])
def test_colour_ranges(level, colour):
    assert battery_colour(level) == colour


def test_tooltip_names_the_device():
    assert battery_tooltip("Razer Naga V2 Pro", 80, False) == "Razer Naga V2 Pro – 80 %"
    assert battery_tooltip("Razer Naga V2 Pro", 55, True) == "Razer Naga V2 Pro – 55 %, charging"


def _has(pm, colour: str, tolerance=40) -> bool:
    want = QColor(colour)
    img = pm.toImage()
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 200 and all(abs(a - b) <= tolerance for a, b in (
                    (c.red(), want.red()), (c.green(), want.green()), (c.blue(), want.blue()))):
                return True
    return False


def test_icon_has_every_tray_size(qapp):
    sizes = {(s.width(), s.height()) for s in battery_icon(80, False).availableSizes()}
    assert {(s, s) for s in SIZES} <= sizes


@pytest.mark.parametrize("level,colour", [(80, GREEN), (18, ORANGE), (6, RED)])
def test_fill_colour_is_painted(qapp, level, colour):
    assert _has(paint_battery(48, level, False), colour)


def test_number_is_painted_white_and_bolt_only_while_charging(qapp):
    plain, charging = paint_battery(48, 50, False), paint_battery(48, 50, True)
    assert _has(plain, "#ffffff", tolerance=10)  # the level as a number

    def top_has_green(pm):  # the bolt sits above the battery body
        img = pm.toImage()
        return any(img.pixelColor(x, y).alpha() > 200 and img.pixelColor(x, y).green() > 150
                   for y in range(0, 12) for x in range(img.width()))
    assert top_has_green(charging) and not top_has_green(plain)


def test_100_percent_fits(qapp):
    paint_battery(16, 100, True)  # must not raise or loop
    paint_battery(16, 7, False)


class FakeIcon:
    def __init__(self, parent=None):
        self.visible, self.tooltip, self.icon, self.deleted = False, "", None, False
        self.sets = 0

    def setIcon(self, icon):
        self.icon = icon
        self.sets += 1

    def setToolTip(self, text):
        self.tooltip = text

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def deleteLater(self):
        self.deleted = True


@pytest.fixture
def env(qapp):
    t = FakeTransport()
    t.statuses[NAGA] = {"connected": True, "battery": 80, "charging": False}
    c = DaemonClient(t)
    c.status()
    bt = BatteryTray(c, icon_factory=FakeIcon)
    return bt, c, t


def test_icon_per_connected_battery_device(env):
    bt, c, t = env
    assert set(bt.icons) == {NAGA}
    assert bt.icons[NAGA].visible and bt.icons[NAGA].tooltip == "Razer Naga V2 Pro – 80 %"
    t.add_second_device()
    t.statuses[DA] = {"connected": True, "battery": 55, "charging": True}
    c.devicesChanged.emit()
    assert set(bt.icons) == {NAGA, DA}
    assert bt.icons[DA].tooltip == "Razer DeathAdder V2 – 55 %, charging"


def test_status_updates_repaint_only_on_change(env):
    bt, c, t = env
    icon = bt.icons[NAGA]
    sets = icon.sets
    c.statusChanged.emit(NAGA, {"connected": True, "battery": 80, "charging": False})
    assert icon.sets == sets
    c.statusChanged.emit(NAGA, {"connected": True, "battery": 79, "charging": True})
    assert icon.sets == sets + 1 and icon.tooltip.endswith("79 %, charging")


def test_no_icon_without_battery_level(env):
    bt, c, t = env
    c.statusChanged.emit(NAGA, {"connected": True, "battery": -1})
    assert bt.icons == {}


def test_icon_goes_when_unplugged_or_daemon_gone(env):
    bt, c, t = env
    icon = bt.icons[NAGA]
    c.statusChanged.emit(NAGA, {"connected": False, "battery": 80})
    assert bt.icons == {} and not icon.visible and icon.deleted
    c.statusChanged.emit(NAGA, {"connected": True, "battery": 80})
    assert NAGA in bt.icons
    t.up = False
    c.status()  # the client notices the daemon is gone
    assert bt.icons == {}


def test_disconnected_device_in_list_gets_no_icon(env):
    bt, c, t = env
    t.devices[0][3] = False
    c.devicesChanged.emit()
    assert bt.icons == {}


def test_switch_off_and_on(env):
    bt, c, t = env
    bt.set_enabled(False)
    assert bt.icons == {}
    c.statusChanged.emit(NAGA, {"connected": True, "battery": 70})
    assert bt.icons == {}
    bt.set_enabled(True)
    assert set(bt.icons) == {NAGA}
