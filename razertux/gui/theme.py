"""Synapse-like dark theme (own QSS + palette, no Razer assets) and the switch back
to the system colours (Settings.theme "synapse" | "system")."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from razertux.config.store import ConfigStore

WINDOW = "#111111"
PANEL = "#1b1b1b"
FIELD = "#232323"
BORDER = "#2e2e2e"
TEXT = "#cfcfcf"
MUTED = "#8a8a8a"
ACCENT = "#44D62C"
ACCENT_DIM = "#1f4a17"

QSS = f"""
QToolTip {{ color: {TEXT}; background: {PANEL}; border: 1px solid {BORDER}; }}
QMenuBar {{ background: {WINDOW}; color: {MUTED}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {FIELD}; color: {ACCENT}; }}
QMenu {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 5px 22px; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 6px 16px;
               margin: 6px 4px; border-radius: 13px; }}
QTabBar::tab:hover:!selected {{ color: #ffffff; }}
QTabBar::tab:selected {{ background: {ACCENT}; color: #000000; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px; }}
QLabel#cardTitle {{ color: {ACCENT}; font-size: 12pt; font-weight: normal;
                   letter-spacing: 1px; background: transparent; }}
QLabel#cardText {{ color: {TEXT}; }}
QLabel#sliderValue {{ color: {ACCENT}; font-weight: bold; }}
QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled {{ color: #5a5a5a; }}
QPushButton {{ background: {FIELD}; color: {TEXT}; border: 1px solid #3a3a3a;
              padding: 5px 14px; border-radius: 2px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:checked {{ border: 1px solid {ACCENT}; color: #ffffff; background: #1a2a17; }}
QPushButton:disabled {{ color: #5a5a5a; border-color: #2a2a2a; }}
QPushButton:flat {{ background: transparent; border: none; }}
QPushButton:flat:hover {{ color: {ACCENT}; }}
QComboBox, QLineEdit, QPlainTextEdit, QTextEdit, QListView, QListWidget,
QTreeView, QTableView, QTableWidget {{ background: {FIELD}; color: {TEXT};
              border: 1px solid #3a3a3a; selection-background-color: {ACCENT};
              selection-color: #000000; }}
QComboBox, QLineEdit {{ padding: 3px 6px; border-radius: 2px; }}
QComboBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QPushButton#iconButton {{ padding: 2px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; }}
/* QSpinBox stays native Fusion (dark palette): QSS would hide its arrows */
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none;
              border-bottom: 1px solid {BORDER}; padding: 4px; }}
QSlider::groove:horizontal {{ height: 4px; background: {ACCENT_DIM}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; height: 14px;
              margin: -5px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal:disabled, QSlider::handle:horizontal:disabled {{ background: #4a4a4a; }}
QSlider::groove:horizontal:disabled {{ background: #2a2a2a; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px;
              border: 1px solid #5a5a5a; background: {FIELD}; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {ACCENT};
              border-color: {ACCENT}; }}
QFrame#stageRow, QFrame#valueTile {{ background: {FIELD}; border: 1px solid {FIELD};
              border-radius: 3px; }}
QFrame#stageRow[active="true"] {{ border-color: {ACCENT}; }}
QFrame#stageRow QLineEdit {{ background: transparent; border: 1px solid transparent; }}
QFrame#stageRow QLineEdit:focus {{ border-color: {ACCENT}; }}
QLabel#stageSlot {{ color: {MUTED}; }}
QLabel#stageMarker {{ color: {ACCENT}; }}
QLabel#activeName {{ color: #ffffff; }}
QLabel#tileValue {{ color: {TEXT}; font-size: 18pt; }}
QWidget#header {{ background: {WINDOW}; border-bottom: 1px solid {BORDER}; }}
QLabel#deviceTitle {{ color: {MUTED}; font-size: 11pt; letter-spacing: 1px; }}
QLabel#mutedText {{ color: {MUTED}; }}
QToolButton#dotsButton {{ color: {TEXT}; background: transparent; border: none; padding: 2px 6px;
              font-weight: bold; }}
QToolButton#dotsButton:hover {{ color: {ACCENT}; }}
QToolButton#dotsButton::menu-indicator {{ image: none; width: 0; }}
QFrame#pill {{ background: {PANEL}; border: 1px solid #3a3a3a; border-radius: 17px; }}
QPushButton#pillButton {{ background: transparent; border: 1px solid transparent; border-radius: 14px;
              padding: 5px 18px; color: {TEXT}; }}
QPushButton#pillButton:hover:!checked {{ color: #ffffff; }}
QPushButton#pillButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: #000000; }}
QPushButton#bindingLabel {{ color: {TEXT}; }}
QPushButton#bindingLabel[hot="true"] {{ color: {ACCENT}; }}
QPushButton#bindingLabel:focus {{ color: {ACCENT}; background: #1a2a17; border: 1px solid {ACCENT_DIM};
              border-radius: 2px; }}
QPushButton#bindingLabel:disabled {{ color: #5a5a5a; }}
QComboBox#languageCombo {{ color: {MUTED}; }}
QScrollBar:vertical {{ background: {WINDOW}; width: 10px; }}
QScrollBar::handle:vertical {{ background: #3a3a3a; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
"""


def dark_palette() -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: WINDOW, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: FIELD, QPalette.ColorRole.AlternateBase: PANEL,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: FIELD,
        QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Highlight: ACCENT, QPalette.ColorRole.HighlightedText: "#000000",
        QPalette.ColorRole.Mid: "#5a5a5a", QPalette.ColorRole.Dark: "#0a0a0a",
        QPalette.ColorRole.Light: "#3a3a3a", QPalette.ColorRole.Midlight: "#2a2a2a",
        QPalette.ColorRole.Shadow: "#000000", QPalette.ColorRole.ToolTipBase: PANEL,
        QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.PlaceholderText: MUTED,
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, colour in roles.items():
        pal.setColor(role, QColor(colour))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5a5a5a"))
    return pal


_original: dict[str, object] = {}


def apply_theme(app: QApplication, name: str) -> None:
    """"synapse": Fusion + dark palette + QSS; anything else: the system look."""
    if not _original:
        _original["style"] = app.style().name()
        _original["palette"] = QPalette(app.palette())
    if name == "synapse":
        app.setStyle(QStyleFactory.create("Fusion"))
        app.setPalette(dark_palette())
        app.setStyleSheet(QSS)
    else:
        app.setStyleSheet("")
        style = QStyleFactory.create(str(_original["style"]))
        if style is not None:
            app.setStyle(style)
        app.setPalette(_original["palette"])  # type: ignore[arg-type]


def save_theme(store: ConfigStore, name: str) -> None:
    """Persist only the theme (the daemon owns active_profile)."""
    s = store.load_settings()
    s.theme = name
    store.save_settings(s)


class ThemeController(QObject):
    """Single owner of the theme: window menu and tray both call set_theme()."""

    changed = Signal(str)

    def __init__(self, app: QApplication, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.app, self.store = app, store
        self.name = store.load_settings().theme

    def apply(self) -> None:
        apply_theme(self.app, self.name)

    def set_theme(self, name: str) -> None:
        if name == self.name:
            return
        self.name = name
        save_theme(self.store, name)
        self.apply()
        self.changed.emit(name)
