"""On-screen display: a rounded square with the new profile name or DPI value in
laser green, shown for a moment when the profile or the DPI changes.

The window never takes focus, is not in the task bar and lets clicks through. Wayland
does not let a client place top-level windows, so only on X11 it is centred
explicitly (KWin puts it in the middle itself)."""
from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPalette, QPen,
)
from PySide6.QtWidgets import QApplication, QWidget

from razertux.config.store import ConfigStore
from razertux.gui.dbus_client import DaemonClient
from razertux.gui.laser import CORE, CORE_FACTOR, CORE_OPACITY, GLOW
from razertux.gui.theme import ACCENT

SIZE = 220
RADIUS = 24
INSET = 12.0           # room for the widest glow stroke
BACKGROUND = QColor("#0b0b0b")
BACKGROUND.setAlphaF(0.85)
VISIBLE_MS = 1500
FADE_MS = 300
TEXT_WIDTH = 192       # widest value text
BORDER_WIDTH = 2.0
TEXT_STROKE = 1.0


class OsdWindow(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(SIZE, SIZE)
        self.value = ""
        self.kind = ""
        self.device: str | None = None
        self.theme = "synapse"   # "system": the palette highlight is the accent
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._fade_out)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.hide)

    # -- API ------------------------------------------------------------------
    def show_message(self, value: str, kind: str, device: str | None = None) -> None:
        """Show at once (replacing a running display), 1.5 s, then fade out."""
        self._fade.stop()
        self._hold.stop()
        self.value, self.kind, self.device = value, kind, device
        self.setWindowOpacity(1.0)
        if QGuiApplication.platformName() == "xcb":
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                self.move(screen.geometry().center() - self.rect().center())
        self.show()
        self.update()
        self._hold.start(VISIBLE_MS)

    def _fade_out(self) -> None:
        self._fade.start()

    # -- painting -------------------------------------------------------------
    def accent(self) -> QColor:
        if self.theme == "system":
            return QApplication.palette().color(QPalette.Highlight)
        return QColor(ACCENT)

    def _kind_text(self) -> str:
        return {"DPI": self.tr("DPI"), "PROFIL": self.tr("PROFIL")}.get(self.kind, self.kind)

    def _value_font(self) -> QFont:
        font = QFont(self.font())
        font.setBold(True)
        size = 46
        while size > 10:
            font.setPixelSize(size)
            if QFontMetrics(font).horizontalAdvance(self.value) <= TEXT_WIDTH:
                break
            size -= 2
        font.setPixelSize(size)
        return font

    def value_font_width(self) -> int:
        return QFontMetrics(self._value_font()).horizontalAdvance(self.value)

    def _glow(self, p: QPainter, path: QPainterPath, accent: QColor, width: float) -> None:
        """The laser recipe: wide faint strokes, the line itself, a pale core."""
        p.setBrush(Qt.NoBrush)
        for factor, opacity in GLOW:
            c = QColor(accent)
            c.setAlphaF(float(opacity))
            p.setPen(QPen(c, width * factor, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
        core = QColor(CORE)
        core.setAlphaF(float(CORE_OPACITY))
        p.setPen(QPen(core, width * CORE_FACTOR, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)

    def _text_path(self, text: str, font: QFont, centre_y: float) -> QPainterPath:
        fm = QFontMetrics(font)
        text = fm.elidedText(text, Qt.ElideRight, TEXT_WIDTH)
        path = QPainterPath()
        x = (SIZE - fm.horizontalAdvance(text)) / 2
        path.addText(x, centre_y + (fm.ascent() - fm.descent()) / 2, font, text)
        return path

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        accent = self.accent()
        box = QRectF(INSET, INSET, SIZE - 2 * INSET, SIZE - 2 * INSET)
        frame = QPainterPath()
        frame.addRoundedRect(box, RADIUS, RADIUS)
        p.setPen(Qt.NoPen)
        p.setBrush(BACKGROUND)
        p.drawPath(frame)
        self._glow(p, frame, accent, BORDER_WIDTH)

        small = QFont(self.font())
        small.setPixelSize(15)
        small.setBold(True)
        small.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        for text, y in ((self._kind_text(), SIZE * 0.70),
                        *(((self.device, SIZE * 0.20),) if self.device else ())):
            path = self._text_path(text, small, y)
            self._glow(p, path, accent, 0.6)
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawPath(path)
        big = self._text_path(self.value, self._value_font(), SIZE * 0.46)
        self._glow(p, big, accent, TEXT_STROKE)
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawPath(big)
        p.end()


class OsdController:
    """Turns profile and DPI changes of the daemon into OSD messages."""

    def __init__(self, client: DaemonClient, store: ConfigStore,
                 theme_name=lambda: "synapse") -> None:
        self.client = client
        self.store = store
        self._theme_name = theme_name
        self.enabled = store.load_settings().osd
        self.window = OsdWindow()
        self._profile: dict[str, str] = {}
        self._dpi: dict[str, int | None] = {}   # None = next status is the baseline
        client.profileChanged.connect(self._on_profile)
        client.statusChanged.connect(self._on_status)
        client.available.connect(lambda _up: self._dpi.clear())

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        s = self.store.load_settings()
        s.osd = on
        self.store.save_settings(s)

    def _show(self, value: str, kind: str, model: str) -> None:
        self.window.theme = self._theme_name()
        device = None
        devices = self.client.devices()
        if sum(1 for d in devices if d[3]) >= 2:
            device = next((d[1] for d in devices if d[0] == model), None)
        self.window.show_message(value, kind, device)

    def _on_profile(self, model: str, profile_id: str) -> None:
        changed = self._profile.get(model) != profile_id
        self._profile[model] = profile_id
        if not (changed and self.enabled):
            return
        name = dict(self.client.list_profiles(model)).get(profile_id, profile_id)
        self._show(name, "PROFIL", model)

    def _on_status(self, model: str, status: dict) -> None:
        if not status.get("connected", False):
            self._dpi.pop(model, None)   # next status after (re)connecting is a baseline
            return
        dpi = int(status.get("dpi", -1))
        if dpi < 0:
            return
        last = self._dpi.get(model)
        self._dpi[model] = dpi
        if last is not None and last != dpi and self.enabled:
            self._show(str(dpi), "DPI", model)
