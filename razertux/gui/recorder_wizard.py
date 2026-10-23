"""Device recorder wizard ("Help → Record device…", tray entry, ``razertux-record``): choose a
Razer device, record its buttons, read a few values, optional light test, review and submit
the report as a prefilled GitHub issue or save it as a file."""
from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from razertux import APP_NAME
from razertux.gui.dbus_client import DaemonClient
from razertux.gui.recorder_buttons import SETTLE_MS, ButtonsPage
from razertux.gui.recorder_pages import (
    ChoosePage, DevicePage, KindPage, LightPage, PermissionPage, ReadsPage, ReviewPage, SendPage,
    TemplatePage,
)
from razertux.gui.recorder_session import RecorderSession, default_backend
from razertux.recorder.backend import BackendError, HelperBackend
from razertux.recorder.discover import list_razer_devices
from razertux.recorder.report import issue_url, sanitize

__all__ = ["RecorderWizard", "open_recorder", "main", "BackendError"]

ORDER = ("choose", "permission", "device", "kind", "template", "buttons", "reads", "light", "review", "send")
STYLE = """
QWidget#recSide { background: #151515; border-right: 1px solid #2e2e2e; }
QLabel#recStep { color: #8a8a8a; padding: 4px 10px; border-radius: 11px; }
QLabel#recStep[state="current"] { color: #000000; background: #44D62C; }
QLabel#recStep[state="done"] { color: #cfcfcf; }
QLabel#recTitle { color: #44D62C; font-size: 16pt; letter-spacing: 1px; }
QLabel#recBrand { color: #8a8a8a; font-size: 11pt; letter-spacing: 2px; }
QLabel#recIndicator { color: #ff5a4a; font-weight: bold; }
QPushButton#templateCard { background: #161616; border: 1px solid #2e2e2e; border-radius: 4px; }
QPushButton#templateCard:checked { border: 2px solid #44D62C; background: #142012; }
"""


class RecorderWizard(QWidget):
    def __init__(self, client: DaemonClient | None = None, parent: QWidget | None = None, *,
                 list_devices: Callable[[], list] | None = None,
                 make_backend: Callable = default_backend,
                 password_availability: Callable[[], tuple[bool, str]] | None = None,
                 clock: Callable[[], float] = time.monotonic, settle_ms: int = SETTLE_MS):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(self.tr("Record device – %1").replace("%1", APP_NAME))
        self.setStyleSheet(STYLE)
        self._list_devices = list_devices or (lambda: list_razer_devices())
        self.session = RecorderSession(client, make_backend, clock, self)
        s = self.session
        self.pages = {
            "choose": ChoosePage(s, lambda: self._list_devices()),
            "permission": PermissionPage(s, password_availability or HelperBackend.availability),
            "device": DevicePage(s), "kind": KindPage(s), "template": TemplatePage(s),
            "buttons": ButtonsPage(s, settle_ms), "reads": ReadsPage(s), "light": LightPage(s),
            "review": ReviewPage(s), "send": SendPage(s, self.submit, self.save),
        }
        self.devices_list = self.pages["choose"].list
        self.page_name = "choose"
        self.visited: set[str] = set()
        self._closed = False
        self._build()
        self.pages["permission"].connected.connect(lambda: self.go(self._step(+1)))
        for page in self.pages.values():
            page.changed.connect(self._update_nav)
        self.go("choose")
        self.resize(1100, 800)

    def _build(self) -> None:
        self.stack = QStackedWidget()
        for page in self.pages.values():
            self.stack.addWidget(page)
        side = QWidget()
        side.setObjectName("recSide")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 20, 16, 20)
        brand = QLabel(self.tr("RECORD DEVICE"))
        brand.setObjectName("recBrand")
        sl.addWidget(brand)
        sl.addSpacing(12)
        self.step_labels: dict[str, QLabel] = {}
        for name in ORDER:
            lab = QLabel(self.pages[name].title)
            lab.setObjectName("recStep")
            self.step_labels[name] = lab
            sl.addWidget(lab)
        sl.addStretch(1)
        self.title = QLabel()
        self.title.setObjectName("recTitle")
        self.device_label = QLabel()
        self.device_label.setObjectName("mutedText")
        self.cancel_btn = QPushButton(self.tr("Cancel"))
        self.back_btn = QPushButton(self.tr("Back"))
        self.next_btn = QPushButton(self.tr("Next"))
        self.cancel_btn.clicked.connect(lambda _=False: self.close())
        self.back_btn.clicked.connect(lambda _=False: self.go(self._step(-1)))
        self.next_btn.clicked.connect(lambda _=False: self.next())
        nav = QHBoxLayout()
        nav.addWidget(self.cancel_btn)
        nav.addStretch(1)
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)
        main = QVBoxLayout()
        main.setContentsMargins(28, 22, 28, 18)
        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.device_label)
        main.addLayout(head)
        main.addSpacing(8)
        main.addWidget(self.stack, 1)
        main.addSpacing(8)
        main.addLayout(nav)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(side)
        lay.addLayout(main, 1)

    # -- navigation ---------------------------------------------------------------------
    def _applies(self, name: str) -> bool:
        s = self.session
        return {"kind": s.dev is not None and s.db_kind is None,
                "template": s.kind == "mouse" and s.info is None,
                "reads": bool(s.opened.get("reads")),
                "light": bool(s.opened.get("light_zones"))}.get(name, True)

    def _step(self, direction: int) -> str:
        i = ORDER.index(self.page_name) + direction
        while 0 <= i < len(ORDER) and not self._applies(ORDER[i]):
            i += direction
        return ORDER[min(max(i, 0), len(ORDER) - 1)]

    def next(self) -> None:
        if self.page_name == "choose":
            dev = self.pages["choose"].selected()
            if dev is None:
                return
            if dev is not self.session.dev:
                self.session.select(dev)
        if self.pages[self.page_name].can_next():
            self.go(self._step(+1))

    def go(self, name: str) -> None:
        if self._closed:
            return
        old = self.pages[self.page_name]
        if name != self.page_name:
            old.leave()
        self.page_name = name
        self.visited.add(name)
        page = self.pages[name]
        self.stack.setCurrentWidget(page)
        page.enter()
        self.title.setText(page.title)
        dev = self.session.dev
        self.device_label.setText(f"{dev.product}  {dev.vid:04x}:{dev.pid:04x}" if dev is not None else "")
        self._update_nav()

    def _update_nav(self) -> None:
        if not hasattr(self, "next_btn"):
            return
        name = self.page_name
        self.back_btn.setEnabled(name != "choose")
        self.next_btn.setVisible(name != "send")
        self.next_btn.setEnabled(name != "send" and self.pages[name].can_next())
        self.cancel_btn.setText(self.tr("Close") if name == "send" else self.tr("Cancel"))
        at = ORDER.index(name)
        for i, (n, lab) in enumerate(self.step_labels.items()):
            lab.setVisible(self._applies(n))
            state = "current" if n == name else "done" if i < at else ""
            if lab.property("state") != state:
                lab.setProperty("state", state)
                lab.style().unpolish(lab)
                lab.style().polish(lab)

    # -- sending -------------------------------------------------------------------------
    def open_url(self, url: str) -> bool:
        return QDesktopServices.openUrl(QUrl(url))

    def submit(self) -> None:
        url, complete = issue_url(self.session.report())
        page = self.pages["send"]
        if not self.open_url(url):
            page.status.setText(self.tr("The browser could not be opened. Please save the report as "
                                        "a file and attach it to a new issue."))
        elif complete:
            page.status.setText(self.tr("The issue form is open in your browser. Check it and press "
                                        "“Create”."))
        else:
            page.status.setText(self.tr(
                "The issue form is open in your browser, but the full data was too long for the "
                "link. Please press “Save as file” and attach the file to the issue (drag it into "
                "the text field)."))

    def save(self) -> None:
        path = Path.home() / self.session.file_name()
        text = json.dumps(sanitize(self.session.report()), indent=2, sort_keys=True, ensure_ascii=False)
        page = self.pages["send"]
        try:
            path.write_text(text + "\n", encoding="utf-8")
        except OSError as e:
            page.status.setText(self.tr("Saving failed: %1").replace("%1", str(e)))
            return
        page.status.setText(self.tr("Saved: %1").replace("%1", str(path)))

    # -- closing -------------------------------------------------------------------------
    def closeEvent(self, e: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(e)

    def shutdown(self) -> None:
        """Stop a recording, resume the daemon, end the helper (also on errors)."""
        if self._closed:
            return
        try:
            self.pages[self.page_name].leave()
        finally:
            self._closed = True
            self.session.close()


_open: RecorderWizard | None = None


def open_recorder(parent: QWidget | None, client: DaemonClient | None) -> RecorderWizard:
    """Show the wizard (one at a time)."""
    global _open
    if _open is None or not _open.isVisible():
        _open = RecorderWizard(client, parent)
    _open.show()
    _open.raise_()
    _open.activateWindow()
    return _open


def _prepare_app(app: QApplication) -> None:
    from razertux.config.migrate import default_config_dir
    from razertux.config.store import ConfigStore
    from razertux.gui.language import install_language
    from razertux.gui.theme import ThemeController
    from razertux.gui.tray import app_icon
    app.setApplicationName("razertux")
    app.setDesktopFileName("razertux")
    store = ConfigStore(default_config_dir())
    install_language(app, store)
    ThemeController(app, store).apply()
    app.setWindowIcon(app_icon())


def _exec(app: QApplication) -> int:
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    """razertux-record: only the recorder wizard."""
    app = QApplication.instance() or QApplication([sys.argv[0]])
    _prepare_app(app)
    wizard = RecorderWizard(DaemonClient())
    wizard.show()
    return _exec(app)


if __name__ == "__main__":
    sys.exit(main())
