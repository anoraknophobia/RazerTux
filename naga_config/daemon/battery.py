"""Battery polling (wireless only) and the one-shot low battery desktop warning."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

REARM_MARGIN = 5  # warn again only after the level rose to threshold + 5


class BatteryMonitor:
    """Polls battery level and charging state every `interval_s` while the mouse is
    connected over the wireless dongle (wired: no queries at all).

    on_change(level, charging) runs whenever a reading differs from the last one.
    notify(level) runs once when the level drops below threshold() (0 = off) while
    not charging; it re-arms when the level is back at threshold + 5 or more."""

    def __init__(self, dev: Any, interval_s: float = 60,
                 on_change: Callable[[int, bool], None] | None = None, *,
                 connection: Callable[[], str] = lambda: "wireless",
                 threshold: Callable[[], int] = lambda: 15,
                 notify: Callable[[int], None] | None = None) -> None:
        self.dev = dev
        self.interval_s = interval_s
        self._on_change = on_change or (lambda b, c: None)
        self._connection = connection
        self._threshold = threshold
        self._notify = notify or (lambda b: None)
        self._last: tuple[int, bool] | None = None
        self._armed = True
        self._task: asyncio.Task | None = None

    def poll(self) -> None:
        """Take one reading (no-op when wired); never raises on device errors."""
        if self._connection() != "wireless":
            return
        try:
            level, charging = int(self.dev.battery()), bool(self.dev.charging())
        except Exception as e:  # HW gone / garbage reply: try again next interval
            logger.debug("Akkustand nicht lesbar: %s", e)
            return
        if (level, charging) != self._last:
            self._last = (level, charging)
            self._on_change(level, charging)
        threshold = self._threshold()
        if threshold <= 0:
            return
        if level >= threshold + REARM_MARGIN:
            self._armed = True
        elif level < threshold and self._armed and not charging:
            self._armed = False
            self._notify(level)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            self.poll()


async def notify_desktop(bus: Any, summary: str, body: str,
                         icon: str = "battery-caution") -> None:
    """Show a desktop notification via org.freedesktop.Notifications."""
    from dbus_next import Message

    await bus.call(Message(
        destination="org.freedesktop.Notifications",
        path="/org/freedesktop/Notifications",
        interface="org.freedesktop.Notifications", member="Notify",
        signature="susssasa{sv}i",
        body=["naga-config", 0, icon, summary, body, [], {}, -1]))
