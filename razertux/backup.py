"""One-time mouse state backup. Never overwrites an existing file."""
import json
import logging
from pathlib import Path

from .razer.mouse import VARSTORE, RazerMouse
from .razer.transport import RazerError

logger = logging.getLogger(__name__)


CORE_FIELDS = ("firmware", "dpi", "poll_rate")


def backup_path(model: str, root: Path) -> Path:
    """Per-device backup file. (The Naga's legacy path root/mouse-backup.json is migrated later.)"""
    return Path(root) / "backups" / f"{model}.json"


class BackupError(Exception):
    pass


def load_backup(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def save_backup(dev: RazerMouse, path: Path) -> dict:
    """Write dev.snapshot() (supported fields only) to path unless it exists;
    returns the file's content."""
    path = Path(path)
    if path.exists():
        return load_backup(path)
    snap = dev.snapshot()
    if all(snap.get(k) is None for k in CORE_FIELDS):
        raise BackupError(
            "Maus liefert keine Kernwerte (Firmware, DPI, Abfragerate); nichts gesichert"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x") as f:  # exclusive: never clobber
            json.dump(snap, f, indent=2)
            f.write("\n")
    except FileExistsError:
        return load_backup(path)
    return snap


_RESTORE_ERRORS = (RazerError, OSError, ValueError, IndexError, KeyError, TypeError)


def restore_backup(dev: RazerMouse, data: dict, fields: set[str] | None = None) -> list[str]:
    """Write the backed-up values back with VARSTORE (they are what the mouse stored
    before we touched it). Never changes the device mode; effects were not backed up
    (not readable), only brightness. Returns the fields that could not be restored,
    in write order: poll_rate, dpi_stages, dpi (after the stages, which reset it),
    brightness.<zone>, idle_time, low_battery_threshold, scroll_mode,
    scroll_acceleration, scroll_smart_reel.

    Only fields the mouse can write (dev.supports("set_<field>")) are considered;
    the others are skipped, not reported. fields limits the restore to these names
    ("brightness" covers every zone); None restores everything."""
    failed: list[str] = []

    def wanted(name: str) -> bool:
        return (fields is None or name in fields) and dev.supports(f"set_{name}")

    def run(name: str, fn, *args, **kw) -> None:
        try:
            fn(*args, **kw)
        except _RESTORE_ERRORS as e:
            logger.warning("Wiederherstellen von %s fehlgeschlagen: %s", name, e)
            failed.append(name)

    def need(name: str):
        """Backup value for a restorable field; records it as failed if absent."""
        if not wanted(name):
            return None
        value = data.get(name)
        if value is None:
            failed.append(name)
        return value

    if (v := need("poll_rate")) is not None:
        run("poll_rate", dev.set_poll_rate, v)
    if (v := need("dpi_stages")) is not None:
        run("dpi_stages", lambda: dev.set_dpi_stages(
            v["active"], [tuple(s) for s in v["stages"]], store=VARSTORE))
    if (v := need("dpi")) is not None:
        run("dpi", lambda: dev.set_dpi(v[0], v[1], store=VARSTORE))
    if fields is None or "brightness" in fields:
        levels = data.get("brightness") or {}
        for zone in dev.brightness_zones():
            level = levels.get(zone)
            if level is not None and dev.supports(f"set_brightness:{zone}"):
                # None: LED not present / never readable
                run(f"brightness.{zone}", dev.set_brightness, zone, level, store=VARSTORE)
    for name in ("idle_time", "low_battery_threshold", "scroll_mode",
                 "scroll_acceleration", "scroll_smart_reel"):
        if (v := need(name)) is not None:
            run(name, getattr(dev, f"set_{name}"), v)
    return failed
