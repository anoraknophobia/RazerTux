"""Only one GUI per user: a second start asks the running one to show its window.

Uses a per-user QLocalServer. `razertux-gui` (no --tray) while an instance runs
-> that instance opens its window, the new process exits. `--tray` -> just exits.
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = f"razertux-gui-{os.getuid()}"
SHOW, PING = b"show\n", b"ping\n"


def _connect(name: str) -> QLocalSocket | None:
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        return sock
    return None


def notify_running(show: bool, name: str = SERVER_NAME) -> bool:
    """True if another instance is running (asked to show its window when `show`)."""
    sock = _connect(name)
    if sock is None:
        return False
    sock.write(SHOW if show else PING)
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def wait_until_gone(name: str = SERVER_NAME, timeout: float = 5.0) -> bool:
    """Restart: wait for the old instance to quit. True when it is gone."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock = _connect(name)
        if sock is None:
            return True
        sock.disconnectFromServer()
        time.sleep(0.1)
    return False


class InstanceServer(QObject):
    """Listens for later starts; emits showRequested on a "show" message."""

    showRequested = Signal()

    def __init__(self, name: str = SERVER_NAME, parent: QObject | None = None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        QLocalServer.removeServer(name)  # stale socket of a crashed instance
        self.listening = self._server.listen(name)

    def _on_connection(self) -> None:
        # read synchronously (the message is one short line, sent right away);
        # no per-socket slots, so nothing can fire on an already deleted socket
        show = False
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock.bytesAvailable() or sock.waitForReadyRead(500):
                show |= bytes(sock.readAll().data()).startswith(SHOW.strip())
            sock.abort()
            sock.deleteLater()
        if show:
            self.showRequested.emit()

    def close(self) -> None:
        self._server.close()
