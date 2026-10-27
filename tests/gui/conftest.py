import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QSystemTrayIcon


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _dispose_qt_objects() -> None:
    """Close and delete every top-level widget and tray icon a test left behind, then run the
    deferred deletes now: Qt objects that outlive the test session are destroyed during interpreter
    shutdown in arbitrary order, which crashed the process (exit 139) after a green run."""
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    for tray in app.findChildren(QSystemTrayIcon):
        tray.hide()
        tray.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture(autouse=True)
def _close_top_level_widgets():
    yield
    _dispose_qt_objects()
