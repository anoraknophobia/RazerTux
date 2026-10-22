"""Entry point: naga-config-gui [--tray] [--config-dir DIR]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from naga_config.config.store import ConfigStore
from naga_config.daemon.main import default_config_dir


def _tr(text: str) -> str:
    from PySide6.QtCore import QCoreApplication
    return QCoreApplication.translate("app", text)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="naga-config-gui",
                                description=_tr("Konfiguration für die Razer Naga V2 Pro"))
    p.add_argument("--tray", action="store_true", help=_tr("nur Tray-Symbol starten (Fenster versteckt)"))
    p.add_argument("--config-dir", type=Path, default=None, help=_tr("Konfigurationsverzeichnis"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from naga_config.gui.dbus_client import DaemonClient
    from naga_config.gui.main_window import MainWindow
    from naga_config.gui.tray import TrayIcon, app_icon

    app = QApplication([sys.argv[0]])
    app.setApplicationName("naga-config")
    app.setApplicationDisplayName(_tr("Naga-Konfiguration"))
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    client = DaemonClient()
    window = MainWindow(ConfigStore(args.config_dir or default_config_dir()), client)
    tray = TrayIcon(client, dialog_parent=window)
    have_tray = QSystemTrayIcon.isSystemTrayAvailable()
    window.hide_on_close = have_tray

    def show_window() -> None:
        window.show()
        window.raise_()
        window.activateWindow()

    def toggle_window() -> None:
        if window.isVisible():
            window.close()
        else:
            show_window()

    def quit_app() -> None:
        if window.request_close():
            app.quit()

    tray.showWindowRequested.connect(show_window)
    tray.toggleWindowRequested.connect(toggle_window)
    tray.quitRequested.connect(quit_app)
    if have_tray:
        tray.show()
    if not args.tray or not have_tray:
        show_window()
    code = app.exec()
    tray.hide()
    return code


if __name__ == "__main__":
    sys.exit(main())
