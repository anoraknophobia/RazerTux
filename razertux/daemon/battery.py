"""Battery polling (wireless only) and the one-shot low battery desktop warning."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

REARM_MARGIN = 5  # warn again only after the level rose to threshold + 5


@dataclass
class WarnState:
    """Whether the low battery warning may fire; owned by the daemon so that a
    reconnect (new monitor) at low battery does not warn again."""
    armed: bool = True


class BatteryMonitor:
    """Polls battery level and charging state every `interval_s` while the mouse is
    connected over the wireless dongle (wired: no queries at all).

    on_change(level, charging) runs whenever a reading differs from the last one.
    notify(level) runs once when the level drops below threshold() (0 = off) while
    not charging; it re-arms when the level is back at threshold + 5 or more.
    on_poll(ok) runs after every wireless reading attempt (False: no answer, e.g. the
    mouse is asleep), before on_change/notify."""

    def __init__(self, dev: Any, interval_s: float = 60,
                 on_change: Callable[[int, bool], None] | None = None, *,
                 connection: Callable[[], str] = lambda: "wireless",
                 threshold: Callable[[], int] = lambda: 15,
                 notify: Callable[[int], None] | None = None,
                 state: WarnState | None = None,
                 on_poll: Callable[[bool], None] | None = None) -> None:
        self.dev = dev
        self._on_poll = on_poll or (lambda ok: None)
        self.interval_s = interval_s
        self._on_change = on_change or (lambda b, c: None)
        self._connection = connection
        self._threshold = threshold
        self._notify = notify or (lambda b: None)
        self._last: tuple[int, bool] | None = None
        self.state = state or WarnState()
        self._task: asyncio.Task | None = None

    def poll(self) -> None:
        """Take one reading (no-op when wired); never raises on device errors."""
        if self._connection() != "wireless":
            return
        try:
            level, charging = int(self.dev.battery()), bool(self.dev.charging())
        except Exception as e:  # HW gone / asleep / garbage reply: try again next interval
            logger.debug("Battery level not readable: %s", e)
            self._on_poll(False)
            return
        self._on_poll(True)
        if (level, charging) != self._last:
            self._last = (level, charging)
            self._on_change(level, charging)
        threshold = self._threshold()
        if threshold <= 0:
            return
        if level >= threshold + REARM_MARGIN:
            self.state.armed = True
        elif level < threshold and self.state.armed and not charging:
            self.state.armed = False
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
            except Exception:  # never block the caller's cleanup (mode reset)
                logger.exception("Battery polling ended with an error")

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                self.poll()
            except Exception:  # callbacks (status signal, notification) must not end the loop
                logger.exception("Battery polling failed")


async def notify_desktop(bus: Any, summary: str, body: str,
                         icon: str = "battery-caution") -> None:
    """Show a desktop notification via org.freedesktop.Notifications."""
    from dbus_next import Message

    await bus.call(Message(
        destination="org.freedesktop.Notifications",
        path="/org/freedesktop/Notifications",
        interface="org.freedesktop.Notifications", member="Notify",
        signature="susssasa{sv}i",
        body=["RazerTux", 0, icon, summary, body, [], {}, -1]))
