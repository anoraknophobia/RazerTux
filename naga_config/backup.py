"""One-time mouse state backup. Never overwrites an existing file."""
import json
import logging
from pathlib import Path

from .razer.naga_v2_pro import VARSTORE, ZONE_LED, NagaV2Pro
from .razer.transport import RazerError

logger = logging.getLogger(__name__)


CORE_FIELDS = ("firmware", "dpi", "poll_rate")


class BackupError(Exception):
    pass


def load_backup(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def save_backup(dev: NagaV2Pro, path: Path) -> dict:
    """Write dev.snapshot() to path unless it exists; returns the file's content."""
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


def restore_backup(dev: NagaV2Pro, data: dict) -> list[str]:
    """Write the backed-up values back with VARSTORE (they are what the mouse stored
    before we touched it). Never changes the device mode; effects were not backed up
    (not readable), only brightness. Returns the fields that could not be restored,
    in write order: poll_rate, dpi_stages, dpi (after the stages, which reset it),
    brightness.<zone>, idle_time, low_battery_threshold."""
    failed: list[str] = []

    def run(name: str, fn, *args, **kw) -> None:
        try:
            fn(*args, **kw)
        except _RESTORE_ERRORS as e:
            logger.warning("Wiederherstellen von %s fehlgeschlagen: %s", name, e)
            failed.append(name)

    def need(name: str):
        """Backup value for a restorable field; records it as failed if absent."""
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
    for zone, led in ZONE_LED.items():
        level = (data.get("brightness") or {}).get(zone)
        if level is not None:  # None: LED not present / never readable
            run(f"brightness.{zone}", dev.set_brightness, led, level, store=VARSTORE)
    if (v := need("idle_time")) is not None:
        run("idle_time", dev.set_idle_time, v)
    if (v := need("low_battery_threshold")) is not None:
        run("low_battery_threshold", dev.set_low_battery_threshold, v)
    return failed
