"""Update notice: once at start and then daily, ask GitHub for the newest release.

A newer version shows a tray message once per version and stays in the tray menu as
"Update available"; its dialog has the update command to copy and a link to the
release notes. Switched off in the tray menu ("Check for updates")."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from razertux import __version__
from razertux.config.store import ConfigStore

REPO = "anoraknophobia/RazerTux"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
UPDATE_COMMAND = (f"curl -fsSL https://raw.githubusercontent.com/{REPO}/main/install.sh | bash")
DAY_MS = 24 * 3600 * 1000
START_DELAY_MS = 30_000  # first check a little after login, not in its busiest moment
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

Fetch = Callable[[str, Callable[[Any], None]], None]  # (url, callback(parsed JSON or None))


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = _VERSION.match(text.strip()) if isinstance(text, str) else None
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def is_newer(tag: str, current: str) -> bool:
    latest, now = parse_version(tag), parse_version(current)
    return latest is not None and now is not None and latest > now


class QtFetch(QObject):
    """GET with QtNetwork; the callback gets the parsed JSON, or None on any error."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtNetwork import QNetworkAccessManager
        self.manager = QNetworkAccessManager(self)

    def __call__(self, url: str, callback: Callable[[Any], None]) -> None:
        from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"RazerTux/{__version__}".encode())
        request.setTransferTimeout(15_000)
        reply = self.manager.get(request)

        def done() -> None:
            data = None
            if reply.error() == QNetworkReply.NetworkError.NoError:
                try:
                    data = json.loads(bytes(reply.readAll().data()).decode())
                except (ValueError, UnicodeDecodeError):
                    data = None
            reply.deleteLater()
            callback(data)

        reply.finished.connect(done)


class UpdateChecker(QObject):
    updateAvailable = Signal(str, str)  # version, release notes URL (every check that finds one)
    notify = Signal(str)  # version: show a tray message (once per version)

    def __init__(self, store: ConfigStore, current: str = __version__,
                 fetch: Fetch | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.current = current
        self._fetch = fetch if fetch is not None else QtFetch(self)
        self.latest: tuple[str, str] | None = None
        self.enabled = store.load_settings().update_check
        self.timer = QTimer(self)
        self.timer.setInterval(DAY_MS)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def start(self) -> None:
        """First check shortly after start (the daily timer runs already)."""
        QTimer.singleShot(START_DELAY_MS, self.check)

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        s = self.store.load_settings()
        s.update_check = on
        self.store.save_settings(s)
        if on:
            self.check()

    def check(self) -> None:
        if self.enabled:
            self._fetch(LATEST_URL, self._on_reply)

    def _on_reply(self, data: Any) -> None:
        if not isinstance(data, dict) or not is_newer(str(data.get("tag_name", "")), self.current):
            return
        version = str(data["tag_name"]).lstrip("v")
        url = str(data.get("html_url") or f"https://github.com/{REPO}/releases")
        self.latest = (version, url)
        self.updateAvailable.emit(version, url)
        s = self.store.load_settings()
        if s.update_notified != version:
            s.update_notified = version
            self.store.save_settings(s)
            self.notify.emit(version)


class UpdateDialog(QDialog):
    def __init__(self, version: str, notes_url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Update available"))
        self.title = QLabel(self.tr("RazerTux %1 is available.").replace("%1", version))
        self.title.setObjectName("cardTitle")
        hint = QLabel(self.tr("To update, run this command in a terminal as your normal user "
                              "(it asks for your password):"))
        hint.setWordWrap(True)
        self.command = QLineEdit(UPDATE_COMMAND)
        self.command.setReadOnly(True)
        self.command.setMinimumWidth(560)
        self.copy_btn = QPushButton(self.tr("Copy"))
        self.copy_btn.clicked.connect(self._copy)
        row = QHBoxLayout()
        row.addWidget(self.command, 1)
        row.addWidget(self.copy_btn)
        self.notes = QLabel(f'<a href="{notes_url}">{self.tr("What is new")}</a>')
        self.notes.setOpenExternalLinks(True)
        close = QPushButton(self.tr("Close"))
        close.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(self.title)
        lay.addWidget(hint)
        lay.addLayout(row)
        lay.addWidget(self.notes)
        lay.addWidget(close)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.command.text())
        self.copy_btn.setText(self.tr("Copied"))
