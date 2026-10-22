"""naga-config command line: backup, status."""
import argparse
import json
import sys
from pathlib import Path

from .backup import save_backup
from .razer.naga_v2_pro import NagaV2Pro
from .razer.transport import HidrawTransport, find_control_hidraw


def default_backup_path() -> Path:
    return Path.home() / ".config" / "naga-config" / "mouse-backup.json"


def _open_transport(hidraw: str | None):
    path = hidraw or find_control_hidraw()
    if path is None:
        raise FileNotFoundError("Keine Naga V2 Pro gefunden (hidraw)")
    return HidrawTransport(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="naga-config")
    ap.add_argument("--hidraw", help="hidraw-Pfad statt Autoerkennung")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Maus-Zustand als JSON ausgeben")
    b = sub.add_parser("backup", help="Maus-Zustand einmalig sichern")
    b.add_argument("--path", type=Path, default=None)
    # allow --hidraw after the subcommand too
    for p in sub.choices.values():
        p.add_argument("--hidraw", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.cmd == "backup":
        path = args.path or default_backup_path()
        if path.exists():
            print(f"Sicherung existiert bereits, nichts überschrieben: {path}", file=sys.stderr)
            return 1
    try:
        with _open_transport(args.hidraw) as t:
            dev = NagaV2Pro(t)
            if args.cmd == "status":
                print(json.dumps(dev.snapshot(), indent=2))
            else:
                save_backup(dev, path)
                print(f"Sicherung geschrieben: {path}")
    except (OSError, FileNotFoundError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
