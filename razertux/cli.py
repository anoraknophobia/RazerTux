"""razertux command line: backup, status, restore."""
import argparse
import asyncio
import gc
import json
import sys
import warnings
from pathlib import Path

from .backup import BackupError, backup_path, load_backup, restore_backup, save_backup
from .config.migrate import default_config_dir, migrate_legacy_config, migrate_schema_2
from .config.model import NAGA_MODEL
from .devices.db import load_devices
from .razer.mouse import make_mouse
from .razer.naga_v2_pro import NagaV2Pro
from .razer.transport import HidrawTransport, find_control_hidraw


VERSION_MISMATCH = "Dienst-Version passt nicht – bitte den RazerTux-Dienst neu starten (systemctl --user restart razertuxd) oder neu anmelden"


def default_backup_path(model: str = NAGA_MODEL) -> Path:
    """The model's backup (the daemon's, per model since config schema 2)."""
    return backup_path(model, default_config_dir())


def _device_info(model: str):
    return load_devices()[model]


def _open_transport(hidraw: str | None):
    path = hidraw or find_control_hidraw()
    if path is None:
        raise FileNotFoundError("Keine Naga V2 Pro gefunden (hidraw)")
    return HidrawTransport(path)


class RestoreError(Exception):
    """The daemon refused or failed the restore (message is user-facing)."""


class ModelError(Exception):
    """No / ambiguous / unknown device model (message is user-facing)."""


async def _daemon_call_async(member: str, *args):
    """Call a Daemon1 method; None if the daemon is not on the bus."""
    from dbus_next import BusType, DBusError, InterfaceNotFoundError, Message
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
        try:
            iface = bus.get_proxy_object(BUS_NAME, OBJECT_PATH, intro).get_interface(INTERFACE)
        except InterfaceNotFoundError as e:  # an older daemon with the single-device API
            raise RestoreError(VERSION_MISMATCH) from e
        return await getattr(iface, member)(*args)
    except DBusError as e:
        raise RestoreError(e.text) from e
    finally:
        bus.disconnect()


def _daemon_call(member: str, *args):
    return asyncio.run(_daemon_call_async(member, *args))


def _dbus_restore(model: str = NAGA_MODEL) -> list[str] | None:
    """Ask the running daemon (the only process that should talk to the mouse) to
    restore. Returns the not-restored fields, or None if the daemon is not on the bus."""
    res = _daemon_call("call_restore_original", model)
    return None if res is None else list(res)


def _connected_models() -> list[str]:
    """Models of the connected mice according to the daemon (none if it is away or old)."""
    try:
        res = _daemon_call("call_list_devices")
    except RestoreError:
        return []
    return [m for m, _name, _conn, up in (res or []) if up]


def resolve_model(model: str | None, hidraw: str | None = None) -> str:
    """--model, else the only connected device (asked from the daemon), else the Naga."""
    known = load_devices()
    if model is not None:
        if model not in known:
            raise ModelError(f"Unbekanntes Modell: {model} (bekannt: {', '.join(known)})")
        return model
    if hidraw is not None:
        return NAGA_MODEL
    connected = _connected_models()
    if len(connected) > 1:
        raise ModelError("Mehrere Geräte verbunden – bitte --model angeben: "
                         + ", ".join(connected))
    return connected[0] if connected else NAGA_MODEL


def _transport_for(args, model: str):
    if model == NAGA_MODEL:
        return _open_transport(args.hidraw)
    info = _device_info(model)
    path = args.hidraw or find_control_hidraw(tuple(info.pids))
    if path is None:
        raise FileNotFoundError(f"Kein {info.name} gefunden (hidraw)")
    return HidrawTransport(path)


def _driver(model: str, transport):
    if model == NAGA_MODEL:
        return NagaV2Pro(transport)
    info = _device_info(model)
    return make_mouse(info, transport, next(iter(info.pids)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="razertux")
    ap.add_argument("--hidraw", help="hidraw-Pfad statt Autoerkennung")
    ap.add_argument("--model", help="Gerätemodell (Standard: das einzige verbundene Gerät)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Maus-Zustand als JSON ausgeben")
    b = sub.add_parser("backup", help="Maus-Zustand einmalig sichern")
    b.add_argument("--path", type=Path, default=None)
    r = sub.add_parser("restore", help="Maus-Zustand aus der Sicherung wiederherstellen")
    r.add_argument("--path", type=Path, default=None)
    # allow --hidraw / --model after the subcommand too
    for p in sub.choices.values():
        p.add_argument("--hidraw", default=argparse.SUPPRESS)
        p.add_argument("--model", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        model = resolve_model(args.model, args.hidraw)
    except ModelError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    if getattr(args, "path", None) is None and args.cmd != "status":
        migrate_legacy_config()  # the default backup may still be in ~/.config/naga-config
        migrate_schema_2(default_config_dir())  # mouse-backup.json -> backups/naga_v2_pro.json

    if args.cmd == "backup":
        path = args.path or default_backup_path(model)
        if path.exists():
            print(f"Sicherung existiert bereits, nichts überschrieben: {path}", file=sys.stderr)
            return 1
    if args.cmd == "restore":
        return _restore(args, model)
    try:
        with _transport_for(args, model) as t:
            dev = _driver(model, t)
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


def _restore(args, model: str = NAGA_MODEL) -> int:
    """Prefer the daemon (RestoreOriginal over D-Bus) so only one process talks to the
    mouse; fall back to hidraw if it is not running. A custom --path can only be
    honoured directly (the daemon always uses the default backup)."""
    try:
        if args.path is None:
            failed = _dbus_restore(model)
            if failed is not None:
                return _report_restore(failed)
        path = args.path or default_backup_path(model)
        try:
            data = load_backup(path)
        except (OSError, ValueError) as e:
            print(f"Fehler: Keine lesbare Sicherung unter {path}: {e}", file=sys.stderr)
            return 1
        with _transport_for(args, model) as t:
            return _report_restore(restore_backup(_driver(model, t), data))
    except (OSError, RestoreError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
