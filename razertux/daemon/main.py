"""razertuxd: one session per connected Razer mouse, remaps via uinput, serves D-Bus.

Run in the foreground: `python -m razertux.daemon.main [--debug]`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from razertux.config.migrate import (
    default_config_dir, migrate_legacy_config, migrate_schema_2,
)
from razertux.config.model import NAGA_MODEL, Profile
from razertux.config.store import ConfigStore
from razertux.devices.db import DeviceInfo, by_pid, load_devices
from razertux.razer.transport import HidrawTransport, find_control_hidraw, find_vendor_hidraw

from .battery import notify_desktop
from .dbus_service import BUS_NAME, OBJECT_PATH, Daemon1Service, openrazer_loaded
from .devices import DeviceWatcher, HidrawReader
from .session import DeviceSession

logger = logging.getLogger("razertux.daemon")

_STATUS_KEYS = ("connected", "connection", "battery", "charging", "dpi", "scroll_stage")


class Daemon:
    """Hotplug routing to one DeviceSession per model, shutdown, D-Bus service.

    The Naga V2 Pro's session always exists. A session stays in `sessions` (listed as
    disconnected) after its mouse is unplugged."""

    def __init__(self, store: ConfigStore, out: Any, *,
                 transport_factory: Callable[[str], Any] = HidrawTransport,
                 find_control: Callable[[tuple[int, ...]], str | None] = find_control_hidraw,
                 find_vendor: Callable[[tuple[int, ...]], str | None] = find_vendor_hidraw,
                 hidraw_reader: Callable[..., Any] = HidrawReader,
                 openrazer_loaded: Callable[[], bool] = openrazer_loaded,
                 apply_device_settings: Callable[[Profile], None] | None = None,
                 retry_delay: float = 0.2) -> None:
        self.store = store
        self.out = out
        self.notify: Callable[[str, str], None] | None = None  # desktop notification
        self._openrazer = openrazer_loaded
        self._session_kw = dict(transport_factory=transport_factory, find_control=find_control,
                                find_vendor=find_vendor, hidraw_reader=hidraw_reader,
                                retry_delay=retry_delay,
                                apply_device_settings=apply_device_settings)
        self._tasks: set[asyncio.Task] = set()
        self.sessions: dict[str, DeviceSession] = {}
        self.service = Daemon1Service(store, lambda: self.sessions,
                                      openrazer_loaded=openrazer_loaded)
        self._session(load_devices()[NAGA_MODEL])

    @property
    def naga(self) -> DeviceSession:
        return self.sessions[NAGA_MODEL]

    def hw_status(self) -> dict[str, Any]:
        """The Naga's hardware state (connected, connection, battery, ...)."""
        status = self.naga.status()
        return {k: status[k] for k in _STATUS_KEYS}

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        self._warn_openrazer()
        await self.service.reload()

    def on_added(self, source: str, dev: Any) -> bool:
        """Route a new input node to its model's session (created on first sight).
        False if the device could not be grabbed (the watcher retries)."""
        pid = dev.info.product
        info = by_pid(pid)
        if info is None:
            logger.info("%s: unbekannte Razer-PID %#06x – ignoriert", dev.path, pid)
            dev.close()
            return True
        session = self.sessions.get(info.model) or self._session(info)
        return session.add_input(pid, source, dev)

    def on_removed(self, path: str) -> None:
        for session in self.sessions.values():
            if session.owns(path):
                session.remove_input(path)

    async def settle(self) -> None:
        """Wait for all background work (hotplug handling) to finish."""
        while self._tasks or any(s._tasks for s in self.sessions.values()):
            await asyncio.gather(*list(self._tasks))
            for session in list(self.sessions.values()):
                await session.settle()

    async def shutdown(self) -> None:
        await self.settle()
        for session in list(self.sessions.values()):
            try:  # one failing session must not keep the others (and uinput) open
                await session.close()
            except Exception:
                logger.exception("Sitzung %s nicht sauber beendet", session.model)
        self.out.close()

    # -- internals -------------------------------------------------------------

    def _session(self, info: DeviceInfo) -> DeviceSession:
        model = info.model
        session = DeviceSession(
            info, self.store.for_model(model), self.out, notify=self._notify,
            warn_openrazer=self._warn_openrazer,
            on_status=lambda: self.service.notify_status(model),
            on_learned=lambda signal: self.service.button_learned(model, signal),
            **self._session_kw)
        self.sessions[model] = session
        if model != NAGA_MODEL:
            logger.info("Neue Sitzung: %s (%s)%s", info.name, model,
                        "" if info.verified else " – experimentell")
            self.service.devices_changed()
        return session

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        async def guarded() -> None:
            try:
                await coro
            except Exception:
                logger.exception("Hintergrundaufgabe fehlgeschlagen")

        task = asyncio.get_running_loop().create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _notify(self, summary: str, body: str) -> None:
        if self.notify is not None:
            self.notify(summary, body)

    def _warn_openrazer(self) -> None:
        if self._openrazer():
            logger.warning("OpenRazer-Treiber (razermouse) ist geladen und kann mit "
                           "RazerTux kollidieren – bitte entladen oder deaktivieren")


async def open_uinput(factory: Callable[[], Any], *, interval: float = 2.0,
                      timeout: float = 60.0,
                      sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> Any:
    """Open the virtual output device. Right after login /dev/uinput may not be usable
    yet (seat ACL not applied): retry every `interval` s for up to `timeout` s, then
    re-raise the last OSError."""
    waited = 0.0
    while True:
        try:
            return factory()
        except OSError as e:
            if waited >= timeout:
                raise
            if waited == 0:
                logger.warning("Kann /dev/uinput noch nicht öffnen (%s) – neuer Versuch alle "
                               "%g s, höchstens %g s lang", e, interval, timeout)
            await sleep(interval)
            waited += interval


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="razertuxd",
                                 description="RazerTux-Dienst (Razer Naga V2 Pro)")
    ap.add_argument("--debug", action="store_true", help="ausführliches Protokoll")
    ap.add_argument("--config-dir", type=Path, default=None,
                    help="Konfigurationsordner (Standard: ~/.config/razertux)")
    return ap


async def run(config_dir: Path) -> int:
    from dbus_next import BusType
    from dbus_next.aio import MessageBus
    from dbus_next.constants import NameFlag, RequestNameReply

    from .uinput_out import UinputOutput

    try:
        out = await open_uinput(UinputOutput)
    except OSError as e:
        logger.error("Kann /dev/uinput nicht öffnen (udev-Regel installiert?): %s", e)
        return 1
    daemon = Daemon(ConfigStore(config_dir), out)
    bus = None
    watcher = DeviceWatcher(daemon.on_added, daemon.on_removed)
    try:
        await daemon.start()
        bus = await MessageBus(bus_type=BusType.SESSION).connect()

        def notify(summary: str, body: str, bus: Any = bus) -> None:
            async def send() -> None:
                try:
                    await notify_desktop(bus, summary, body)
                except Exception as e:  # no notification daemon running
                    logger.warning("Benachrichtigung nicht gesendet: %s", e)
            daemon._spawn(send())
        daemon.notify = notify
        bus.export(OBJECT_PATH, daemon.service)
        reply = await bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE)
        if reply != RequestNameReply.PRIMARY_OWNER:
            logger.error("%s ist bereits belegt – läuft der Dienst schon?", BUS_NAME)
            return 1
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        watcher.start()  # monitor first, then scan: nothing is missed
        watcher.scan()
        logger.info("razertuxd läuft")
        await stop.wait()
        logger.info("Beende …")
        return 0
    finally:
        watcher.stop()
        await daemon.shutdown()
        if bus is not None:
            bus.disconnect()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr,
                        level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.config_dir is None:
        migrate_legacy_config()  # once: ~/.config/naga-config -> ~/.config/razertux
    config_dir = args.config_dir or default_config_dir()
    migrate_schema_2(config_dir)  # profiles per model, after the legacy copy
    return asyncio.run(run(config_dir))


if __name__ == "__main__":
    sys.exit(main())
