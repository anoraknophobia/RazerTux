"""One-time mouse state backup. Never overwrites an existing file."""
import json
from pathlib import Path

from .razer.naga_v2_pro import NagaV2Pro


def load_backup(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def save_backup(dev: NagaV2Pro, path: Path) -> dict:
    """Write dev.snapshot() to path unless it exists; returns the file's content."""
    path = Path(path)
    if path.exists():
        return load_backup(path)
    snap = dev.snapshot()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x") as f:  # exclusive: never clobber
            json.dump(snap, f, indent=2)
            f.write("\n")
    except FileExistsError:
        return load_backup(path)
    return snap
