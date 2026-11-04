"""Entry point: razertux-gui [--tray] [--config-dir DIR]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from razertux import i18n
from razertux.config.store import ConfigStore
from razertux import APP_NAME
from razertux.config.migrate import (
    default_config_dir, migrate_legacy_config, migrate_schema_2,
)


def _help_tr(text: str) -> str:
    """--help runs before Qt: plain-Python catalogs, language from the default config."""
    return i18n.tr("app", text)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="razertux-gui",
                                description=_help_tr("Configuration for the Razer Naga V2 Pro"))
    p.add_argument("--tray", action="store_true",
                   help=_help_tr("start only the tray icon (window hidden)"))
    p.add_argument("--config-dir", type=Path, default=None, help=_help_tr("configuration directory"))
    p.add_argument("--restarted", action="store_true", help=argparse.SUPPRESS)  # language restart
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not any(a == "--config-dir" or a.startswith("--config-dir=") for a in argv):
        if migrate_legacy_config():  # once: ~/.config/naga-config -> ~/.config/razertux
            print("RazerTux: copied ~/.config/naga-config to ~/.config/razertux", file=sys.stderr)
    if "-h" in argv or "--help" in argv:
        i18n.apply_settings(ConfigStore(default_config_dir()).load_settings())
    args = parse_args(argv)
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from razertux.gui.battery_icon import BatteryTray
    from razertux.gui.dbus_client import DaemonClient
    from razertux.gui.language import (
        LanguageController, install_language, restart_args, start_new_instance,
    )
    from razertux.gui.main_window import MainWindow
    from razertux.gui.osd import OsdController
    from razertux.gui.recorder_wizard import open_recorder
    from razertux.gui.theme import ThemeController
    from razertux.gui.tray import TrayIcon, app_icon
    from razertux.gui.updates import UpdateChecker, UpdateDialog

    from razertux.gui.single_instance import InstanceServer, notify_running, wait_until_gone

    app = QApplication([sys.argv[0]])
    if args.restarted:
        wait_until_gone()  # the old instance quits right after starting us
    if notify_running(show=not args.tray):
        return 0  # already running: it shows its window (unless --tray)
    instance = InstanceServer()
    app.setApplicationName("razertux")
    app.setDesktopFileName("razertux")  # Wayland: window icon and task bar entry
    app.setQuitOnLastWindowClosed(False)
    migrate_schema_2(args.config_dir or default_config_dir())  # profiles per model
    store = ConfigStore(args.config_dir or default_config_dir())
    install_language(app, store)  # before any text is created
    app.setApplicationDisplayName(APP_NAME)
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
    osd = OsdController(client, store, theme_name=lambda: theme.name)
    tray.set_osd_checked(osd.enabled)
    tray.osdRequested.connect(osd.set_enabled)
    battery = BatteryTray(client, store.load_settings().battery_icon)
    tray.set_battery_icon_checked(battery.enabled)

    def set_battery_icon(on: bool) -> None:
        battery.set_enabled(on)
        s = store.load_settings()
        s.battery_icon = on
        store.save_settings(s)
        tray.set_battery_icon_checked(on)

    tray.batteryIconRequested.connect(set_battery_icon)
    updates = UpdateChecker(store)
    tray.set_update_check_checked(updates.enabled)
    update_dialog: list[UpdateDialog] = []  # the open one, kept alive

    def show_update() -> None:
        if updates.latest is None:
            return
        if update_dialog:
            update_dialog[0].close()
        update_dialog[:] = [UpdateDialog(*updates.latest, parent=window)]
        update_dialog[0].show()

    def set_update_check(on: bool) -> None:
        updates.set_enabled(on)
        tray.set_update_check_checked(on)
        if not on:
            tray.set_update(None)

    updates.updateAvailable.connect(lambda version, _url: tray.set_update(version))
    updates.notify.connect(lambda version: tray.showMessage(
        APP_NAME, QCoreApplication.translate("UpdateDialog", "RazerTux %1 is available.")
        .replace("%1", version)))
    tray.messageClicked.connect(show_update)
    tray.updateRequested.connect(show_update)
    tray.updateCheckRequested.connect(set_update_check)
    updates.start()
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
    tray.recordRequested.connect(lambda: open_recorder(window, client))
    instance.showRequested.connect(show_window)
    if have_tray:
        tray.show()
    if not args.tray or not have_tray:
        show_window()
    code = app.exec()
    tray.hide()
    return code


if __name__ == "__main__":
    sys.exit(main())
