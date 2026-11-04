"""Battery icon in the tray: one per connected device that reports a battery level.

The icon is a battery outline with a fill bar and the level as a number; green above
20 %, orange from 20 %, red from 10 %. While charging a lightning bolt sits above the
battery. The tooltip names the device, so several icons cannot be mixed up."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon

from razertux.gui.theme import ACCENT

GREEN, ORANGE, RED = ACCENT, "#f0a020", "#e53935"
OUTLINE = "#e0e0e0"
SIZES = (16, 22, 24, 32, 48)
VIEW = 36.0  # drawing units; scaled to each size
BODY = QRectF(1.5, 10.0, 29.0, 20.0)  # battery outline (the tip sits to its right)
TIP = QRectF(30.5, 15.5, 3.5, 9.0)
INNER = BODY.adjusted(2.5, 2.5, -2.5, -2.5)  # where the fill bar goes
TEXT_WIDTH = 25.0  # widest number in drawing units


def _tr(text: str) -> str:
    return QCoreApplication.translate("BatteryIcon", text)


def battery_colour(level: int) -> str:
    """Fill colour: green above 20 %, orange from 20 %, red from 10 %."""
    if level <= 10:
        return RED
    if level <= 20:
        return ORANGE
    return GREEN


def battery_tooltip(name: str, level: int, charging: bool) -> str:
    text = f"{name} – {level} %"
    return text + ", " + _tr("charging") if charging else text


def _bolt() -> QPainterPath:
    """Lightning bolt above the battery (y 0-9.5), right half."""
    path = QPainterPath(QPointF(24.0, 0.0))
    for x, y in ((17.5, 5.5), (21.0, 5.5), (18.5, 10.0), (26.0, 4.0), (22.5, 4.0), (25.5, 0.0)):
        path.lineTo(x, y)
    path.closeSubpath()
    return path


def _number(text: str) -> QPainterPath:
    """The level as an outline path, as large as fits the battery body."""
    font = QFont()
    font.setBold(True)
    size = 15.0
    while True:
        font.setPixelSize(int(size))
        if QFontMetricsF(font).horizontalAdvance(text) <= TEXT_WIDTH or size <= 6:
            break
        size -= 0.5
    path = QPainterPath()
    path.addText(0, 0, font, text)
    box = path.boundingRect()
    centre = BODY.center()
    path.translate(centre.x() - box.center().x(), centre.y() - box.center().y())
    return path


def paint_battery(size: int, level: int, charging: bool) -> QPixmap:
    level = max(0, min(100, level))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / VIEW, size / VIEW)
    colour = QColor(battery_colour(level))
    outline = colour if level <= 10 else QColor(OUTLINE)
    shadow = QColor(0, 0, 0, 160)  # keeps the outline visible on light panels
    for pen in (QPen(shadow, 4.0), QPen(outline, 2.2)):
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(BODY, 3.0, 3.0)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(outline)
    p.drawRoundedRect(TIP, 1.0, 1.0)
    fill = QRectF(INNER)
    fill.setWidth(max(1.5, INNER.width() * level / 100))
    p.setBrush(colour)
    p.drawRoundedRect(fill, 1.5, 1.5)
    number = _number(str(level))
    p.setPen(QPen(QColor(0, 0, 0, 220), 2.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(number)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawPath(number)
    if charging:
        bolt = _bolt()
        p.setPen(QPen(shadow, 1.6))
        p.setBrush(QColor(GREEN))
        p.drawPath(bolt)
    p.end()
    return pm


def battery_icon(level: int, charging: bool) -> QIcon:
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(paint_battery(size, level, charging))
    return icon


class BatteryTray(QObject):
    """Keeps one tray icon per connected device with a known battery level."""

    def __init__(self, client: Any, enabled: bool = True, parent: QObject | None = None,
                 icon_factory: Any = QSystemTrayIcon) -> None:
        super().__init__(parent)
        self.client = client
        self.enabled = enabled
        self._factory = icon_factory
        self.icons: dict[str, Any] = {}
        self._shown: dict[str, tuple[int, bool, str]] = {}  # model -> what is painted
        self._names: dict[str, str] = {}
        client.statusChanged.connect(self._on_status)
        client.devicesChanged.connect(self.refresh)
        client.available.connect(self._on_available)
        self.refresh()

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        self.refresh()

    def refresh(self) -> None:
        """Re-read devices and statuses: icons for connected battery devices only."""
        if not self.enabled or not self.client.is_available:
            self._clear()
            return
        devices = [d for d in self.client.devices() if d[3]]
        self._names = {model: name for model, name, *_ in devices}
        for model in list(self.icons):
            if model not in self._names:
                self._remove(model)
        for model in self._names:
            self._update(model, self.client.status(model))

    def _on_available(self, up: bool) -> None:
        if up:
            self.refresh()
        else:
            self._clear()

    def _on_status(self, model: str, status: dict) -> None:
        if model not in self._names:
            self.refresh()  # a device we have not seen yet
            return
        self._update(model, status)

    def _update(self, model: str, status: dict) -> None:
        level = int(status.get("battery", -1)) if status else -1
        if not self.enabled or not status.get("connected", True) or level < 0:
            self._remove(model)
            return
        charging = bool(status.get("charging", False))
        name = self._names.get(model, model)
        state = (level, charging, name)
        icon = self.icons.get(model)
        if icon is None:
            icon = self.icons[model] = self._factory(self)
        if self._shown.get(model) != state:
            self._shown[model] = state
            icon.setIcon(battery_icon(level, charging))
            icon.setToolTip(battery_tooltip(name, level, charging))
        icon.show()

    def _remove(self, model: str) -> None:
        icon = self.icons.pop(model, None)
        self._shown.pop(model, None)
        if icon is not None:
            icon.hide()
            icon.deleteLater()

    def _clear(self) -> None:
        for model in list(self.icons):
            self._remove(model)
