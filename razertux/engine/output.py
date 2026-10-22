"""Output interface the engine writes to (implemented by the uinput device)."""
from __future__ import annotations

from typing import Protocol


class Output(Protocol):
    def key(self, code: int, value: int) -> None:
        """Emit a key/button event: value 1=down, 0=up, 2=autorepeat (bool works too)."""

    def rel(self, axis: int, value: int) -> None:
        """Emit a relative axis event."""

    def sync(self) -> None:
        """Terminate the current event batch."""
