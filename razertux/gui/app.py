"""Entry point: naga-config-gui [--tray] [--config-dir DIR]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from razertux import i18n
from razertux.config.store import ConfigStore
from razertux.daemon.main import default_config_dir


def _tr(text: str) -> str:
    from PySide6.QtCore import QCoreApplication
    return QCoreApplication.translate("app", text)


def _help_tr(text: str) -> str:
    """--help runs before Qt: plain-Python catalogs, language from the default config."""
    return i18n.tr("app", text)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="naga-config-gui",
                                description=_help_tr("Konfiguration für die Razer Naga V2 Pro"))
    p.add_argument("--tray", action="store_true",
                   help=_help_tr("nur Tray-Symbol starten (Fenster versteckt)"))
    p.add_argument("--config-dir", type=Path, default=None, help=_help_tr("Konfigurationsverzeichnis"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if "-h" in argv or "--help" in argv:
        i18n.apply_settings(ConfigStore(default_config_dir()).load_settings())
    args = parse_args(argv)
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from razertux.gui.dbus_client import DaemonClient
    from razertux.gui.language import (
        LanguageController, install_language, restart_args, start_new_instance,
    )
    from razertux.gui.main_window import MainWindow
    from razertux.gui.theme import ThemeController
    from razertux.gui.tray import TrayIcon, app_icon

    app = QApplication([sys.argv[0]])
    app.setApplicationName("naga-config")
    app.setQuitOnLastWindowClosed(False)
    store = ConfigStore(args.config_dir or default_config_dir())
    install_language(app, store)  # before any text is created
    app.setApplicationDisplayName(_tr("Naga-Konfiguration"))
    theme = ThemeController(app, store)
    theme.apply()  # before any widget: the SVGs take their colours from the palette
    app.setWindowIcon(app_icon())

    client = DaemonClient()
    window = MainWindow(store, client)
    tray = TrayIcon(client, dialog_parent=window)
    for view in (window, tray):
        view.set_theme_checked(theme.name)
        view.themeRequested.connect(theme.set_theme)
        theme.changed.connect(view.set_theme_checked)
    language = LanguageController(store, dialog_parent=window)
    for view in (window, tray):
        view.set_language_checked(language.name)
        view.languageRequested.connect(language.set_language)
        language.changed.connect(view.set_language_checked)
    language.changed.connect(lambda _name: client.reload())  # daemon texts follow on Reload
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

    def restart() -> None:
        if window.request_close() and start_new_instance(restart_args(argv, window.isVisible())):
            app.quit()

    language.restartRequested.connect(restart)
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
