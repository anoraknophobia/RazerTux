"""razertux command line: backup, status, restore."""
import argparse
import asyncio
import gc
import json
import sys
import warnings
from pathlib import Path

from .backup import BackupError, load_backup, restore_backup, save_backup
from .config.migrate import default_config_dir, migrate_legacy_config
from .razer.naga_v2_pro import NagaV2Pro
from .razer.transport import HidrawTransport, find_control_hidraw


def default_backup_path() -> Path:
    return default_config_dir() / "mouse-backup.json"


def _open_transport(hidraw: str | None):
    path = hidraw or find_control_hidraw()
    if path is None:
        raise FileNotFoundError("Keine Naga V2 Pro gefunden (hidraw)")
    return HidrawTransport(path)


class RestoreError(Exception):
    """The daemon refused or failed the restore (message is user-facing)."""


async def _dbus_restore_async() -> list[str] | None:
    from dbus_next import BusType, DBusError, Message
    from dbus_next.aio import MessageBus

    from .daemon.dbus_service import BUS_NAME, INTERFACE, OBJECT_PATH

    try:
        bus = MessageBus(bus_type=BusType.SESSION)  # opens the socket
    except Exception:  # no session bus / bad address: behave as "daemon not running"
        bus = None
    if bus is None:
        # dbus-next leaks the half-open socket when its constructor fails
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            gc.collect()
        return None
    try:
        await bus.connect()
    except Exception:
        bus.disconnect()
        return None
    try:
        reply = await bus.call(Message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="NameHasOwner",
            signature="s", body=[BUS_NAME]))
        if not reply.body or not reply.body[0]:
            return None
        intro = await bus.introspect(BUS_NAME, OBJECT_PATH)
        iface = bus.get_proxy_object(BUS_NAME, OBJECT_PATH, intro).get_interface(INTERFACE)
        return list(await iface.call_restore_original())
    except DBusError as e:
        raise RestoreError(e.text) from e
    finally:
        bus.disconnect()


def _dbus_restore() -> list[str] | None:
    """Ask the running daemon (the only process that should talk to the mouse) to
    restore. Returns the not-restored fields, or None if the daemon is not on the bus."""
    return asyncio.run(_dbus_restore_async())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="razertux")
    ap.add_argument("--hidraw", help="hidraw-Pfad statt Autoerkennung")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Maus-Zustand als JSON ausgeben")
    b = sub.add_parser("backup", help="Maus-Zustand einmalig sichern")
    b.add_argument("--path", type=Path, default=None)
    r = sub.add_parser("restore", help="Maus-Zustand aus der Sicherung wiederherstellen")
    r.add_argument("--path", type=Path, default=None)
    # allow --hidraw after the subcommand too
    for p in sub.choices.values():
        p.add_argument("--hidraw", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if getattr(args, "path", None) is None and args.cmd != "status":
        migrate_legacy_config()  # the default backup may still be in ~/.config/naga-config

    if args.cmd == "backup":
        path = args.path or default_backup_path()
        if path.exists():
            print(f"Sicherung existiert bereits, nichts überschrieben: {path}", file=sys.stderr)
            return 1
    if args.cmd == "restore":
        return _restore(args)
    try:
        with _open_transport(args.hidraw) as t:
            dev = NagaV2Pro(t)
            if args.cmd == "status":
                print(json.dumps(dev.snapshot(), indent=2))
            else:
                snap = save_backup(dev, path)
                missing = [k for k, v in snap.items() if v is None]
                if missing:
                    print(
                        "Warnung: nicht lesbare Felder: " + ", ".join(missing),
                        file=sys.stderr,
                    )
                print(f"Sicherung geschrieben: {path}")
    except (OSError, BackupError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    return 0


def _report_restore(failed: list[str]) -> int:
    if failed:
        print("Nicht wiederhergestellt: " + ", ".join(failed), file=sys.stderr)
        return 2
    print("Wiederhergestellt.")
    return 0


def _restore(args) -> int:
    """Prefer the daemon (RestoreOriginal over D-Bus) so only one process talks to the
    mouse; fall back to hidraw if it is not running. A custom --path can only be
    honoured directly (the daemon always uses the default backup)."""
    try:
        if args.path is None:
            failed = _dbus_restore()
            if failed is not None:
                return _report_restore(failed)
        path = args.path or default_backup_path()
        try:
            data = load_backup(path)
        except (OSError, ValueError) as e:
            print(f"Fehler: Keine lesbare Sicherung unter {path}: {e}", file=sys.stderr)
            return 1
        with _open_transport(args.hidraw) as t:
            return _report_restore(restore_backup(NagaV2Pro(t), data))
    except (OSError, RestoreError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
