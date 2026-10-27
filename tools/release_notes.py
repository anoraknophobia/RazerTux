#!/usr/bin/env python3
"""Print the CHANGELOG.md section of one version (the GitHub release notes).

    release_notes.py 0.4.1         notes of version 0.4.1 (a leading "v" is fine)
    release_notes.py --versions    all versions, newest first
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
_HEAD = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}\s*$")


def sections(text: str) -> dict[str, str]:
    """{version: notes} in file order (newest first)."""
    out: dict[str, str] = {}
    current, lines = None, []
    for line in text.splitlines():
        m = _HEAD.match(line)
        if m or line.startswith("## "):
            if current is not None:
                out[current] = "\n".join(lines).strip() + "\n"
            current, lines = (m.group(1) if m else None), []
        elif current is not None:
            lines.append(line)
    if current is not None:
        out[current] = "\n".join(lines).strip() + "\n"
    return out


def main(argv: list[str]) -> int:
    all_ = sections(CHANGELOG.read_text(encoding="utf-8"))
    if argv == ["--versions"]:
        print("\n".join(all_))
        return 0
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[0].removeprefix("v")
    if version not in all_:
        print(f"CHANGELOG.md has no section for {version}", file=sys.stderr)
        return 1
    sys.stdout.write(all_[version])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
