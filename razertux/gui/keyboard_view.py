"""Razer keyboards (untested): a laser-style picture and a page that offers the recorder.

RazerTux does not configure keyboards yet and never reads their input: the page only shows
what the device data tells (name, USB ID, a picture of the probable form factor) and opens
the device recorder for it. The form factor is guessed from the model name; the picture is a
generic ANSI layout of that size, not the exact keyboard."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from razertux.devices.db import by_pid_any
from razertux.gui.laser import laserize
from razertux.gui.widgets import Card, description
from razertux.recorder.discover import FoundDevice

FORM_FACTORS = ("full", "tkl", "75", "60")
Key = tuple[float, float, float, float]  # x, y, width, height in key units


def form_factor(name: str) -> str:
    """Probable size from the model name: "60" (Mini), "75" (75 %, laptops), "tkl"
    (Tenkeyless) or "full"."""
    n = f" {name.casefold()} "
    if " mini " in n or "60%" in n or " 60 " in n:
        return "60"
    if "75%" in n or " 75 " in n or " blade " in n or "laptop" in n:
        return "75"
    if "tenkeyless" in n or " tkl " in n or " te " in n:
        return "tkl"
    return "full"


def _row(x: float, y: float, widths: list[float]) -> list[Key]:
    keys = []
    for w in widths:
        if w > 0:
            keys.append((x, y, w, 1.0))
        x += abs(w)
    return keys


def _main(y: float) -> list[Key]:
    """The alphanumeric block (5 rows, 15 units wide); negative widths are gaps."""
    return (_row(0, y, [1] * 13 + [2])
            + _row(0, y + 1, [1.5] + [1] * 12 + [1.5])
            + _row(0, y + 2, [1.75] + [1] * 11 + [2.25])
            + _row(0, y + 3, [2.25] + [1] * 10 + [2.75])
            + _row(0, y + 4, [1.25] * 3 + [6.25] + [1.25] * 4))


def _f_row() -> list[Key]:
    return _row(0, 0, [1, -1, 1, 1, 1, 1, -0.5, 1, 1, 1, 1, -0.5, 1, 1, 1, 1])


def _nav(x: float) -> list[Key]:
    return (_row(x, 0, [1, 1, 1]) + _row(x, 1.5, [1, 1, 1]) + _row(x, 2.5, [1, 1, 1])
            + _row(x + 1, 4.5, [1]) + _row(x, 5.5, [1, 1, 1]))


def _numpad(x: float) -> list[Key]:
    return (_row(x, 1.5, [1, 1, 1, 1]) + _row(x, 2.5, [1, 1, 1]) + [(x + 3, 2.5, 1, 2)]
            + _row(x, 3.5, [1, 1, 1]) + _row(x, 4.5, [1, 1, 1]) + [(x + 3, 4.5, 1, 2)]
            + _row(x, 5.5, [2, 1]))


def keys_for(form: str) -> list[Key]:
    """Key rectangles of a generic ANSI keyboard of that size."""
    if form == "60":
        return _main(0)
    if form == "75":  # compact: F row, a right column, arrows tucked in (16 units wide)
        return (_row(0, 0, [1] * 16)
                + _row(0, 1, [1] * 13 + [2, 1])
                + _row(0, 2, [1.5] + [1] * 12 + [1.5, 1])
                + _row(0, 3, [1.75] + [1] * 11 + [2.25, 1])
                + _row(0, 4, [2.25] + [1] * 10 + [1.75, 1, 1])
                + _row(0, 5, [1.25] * 3 + [6.25] + [1] * 6))
    keys = _f_row() + _main(1.5)
    keys += _nav(15.25)
    if form == "full":
        keys += _numpad(18.5)
    return keys


def keyboard_svg(form: str) -> str:
    """Line art in the laser asset format (strokes only, ``@ACC@`` = line colour)."""
    keys = keys_for(form)
    w = max(x + kw for x, _y, kw, _h in keys)
    h = max(y + kh for _x, y, _w, kh in keys)
    u, pad = 20.0, 12.0
    rects = "".join(
        f'<rect x="{pad + x * u + 1.5:g}" y="{pad + y * u + 1.5:g}" width="{kw * u - 3:g}" '
        f'height="{kh * u - 3:g}" rx="3" stroke="@ACC@" stroke-width="1"/>'
        for x, y, kw, kh in keys)
    case = (f'<rect x="2" y="2" width="{w * u + 2 * pad - 4:g}" height="{h * u + 2 * pad - 4:g}" '
            f'rx="10" stroke="@ACC@" stroke-width="1.4"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w * u + 2 * pad:g} '
            f'{h * u + 2 * pad:g}">{case}{rects}</svg>')


class KeyboardPicture(QWidget):
    """The generic keyboard of a form factor in the laser look, scaled to fit."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.form = "full"
        self._renderer: QSvgRenderer | None = None
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_form(self, form: str) -> None:
        self.form = form if form in FORM_FACTORS else "full"
        self._renderer = None
        self.update()

    def changeEvent(self, e) -> None:
        if e.type() == e.Type.PaletteChange:
            self._renderer = None
        super().changeEvent(e)

    def paintEvent(self, _e) -> None:
        if self._renderer is None:
            accent = self.palette().color(QPalette.ColorRole.Highlight).name()
            svg = laserize(keyboard_svg(self.form).replace("@ACC@", accent), accent, None)
            self._renderer = QSvgRenderer(svg.encode("utf-8"))
        size = self._renderer.defaultSize()
        area = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        scale = min(area.width() / size.width(), area.height() / size.height())
        w, h = size.width() * scale, size.height() * scale
        target = QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._renderer.render(p, target)


def keyboard_name(dev: FoundDevice) -> str:
    info = by_pid_any(dev.pid)
    return info.name if info is not None else (dev.product or "Razer keyboard")


class KeyboardPage(QWidget):
    """What RazerTux knows about a connected Razer keyboard, and "Record this keyboard"."""

    recordRequested = Signal(object)  # FoundDevice

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.device: FoundDevice | None = None
        card = Card(self.tr("Keyboard (untested)"))
        self.title = QLabel()
        self.title.setObjectName("activeName")
        font = self.title.font()
        font.setPointSize(max(font.pointSize(), 9) * 2)
        self.title.setFont(font)
        self.details = QLabel()
        self.details.setObjectName("mutedText")
        self.picture = KeyboardPicture()
        card.add(self.title)
        card.add(self.details)
        card.add(description(self.tr(
            "RazerTux recognises this keyboard but cannot configure keyboards yet, and it has "
            "not been tested with one. The picture shows a typical keyboard of this size, not "
            "the exact model. Recording it helps to add it: the recorder collects its keys and "
            "device data and opens a prefilled report on GitHub.")))
        card.add(self.picture)
        self.record_btn = QPushButton(self.tr("Record this keyboard …"))
        self.record_btn.clicked.connect(lambda _=False: self.device is not None
                                        and self.recordRequested.emit(self.device))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.record_btn)
        card.add(row)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(card, 1)

    def set_device(self, dev: FoundDevice) -> None:
        self.device = dev
        name = keyboard_name(dev)
        form = form_factor(name)
        self.title.setText(name)
        sizes = {"full": self.tr("full size"), "tkl": self.tr("tenkeyless"),
                 "75": self.tr("75 %"), "60": self.tr("60 %")}
        self.details.setText(self.tr("USB ID %1 · probably %2").replace(
            "%1", f"{dev.vid:04x}:{dev.pid:04x}").replace("%2", sizes[form]))
        self.picture.set_form(form)


def connected_keyboards(list_devices: Callable[[], list[FoundDevice]]) -> list[FoundDevice]:
    """Connected Razer devices the database lists as keyboards (sysfs only, no access)."""
    out = []
    for dev in list_devices():
        info = by_pid_any(dev.pid)
        if info is not None and info.kind == "keyboard":
            out.append(dev)
    return out
