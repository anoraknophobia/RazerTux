"""naga-configd: grabs the Naga, remaps via uinput, serves D-Bus.

Run in the foreground: `python -m naga_config.daemon.main [--debug]`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from naga_config.config.model import Profile, default_profile
from naga_config.config.store import ConfigStore
from naga_config.engine.mapper import Mapper
from naga_config.hw.buttons import DRIVER_MODE, NORMAL_MODE
from naga_config.razer.naga_v2_pro import NagaV2Pro
from naga_config.razer.transport import (
    HidrawTransport,
    RazerError,
    find_control_hidraw,
    find_vendor_hidraw,
)

from .dbus_service import BUS_NAME, OBJECT_PATH, NagaService, openrazer_loaded
from .devices import DeviceWatcher, HidrawReader, InputRouter

logger = logging.getLogger("naga_config.daemon")

CONNECTION = {0x00A7: "wired", 0x00A8: "wireless"}
_DISCONNECTED = {"connected": False, "connection": "", "battery": -1, "charging": False}
_HW_ERRORS = (RazerError, OSError, ValueError, IndexError)


def default_config_dir() -> Path:
    return Path.home() / ".config" / "naga-config"


def apply_device_settings(profile: Profile) -> None:
    """Hook for per-profile device settings (DPI, poll rate, lighting).
    TODO(Task 7): implement; no-op for now."""


def _safe(fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except _HW_ERRORS as e:
        logger.debug("Abfrage fehlgeschlagen: %s", e)
        return default


class Daemon:
    """Wires devices, mapper, mouse control channel and D-Bus service together."""

    def __init__(self, store: ConfigStore, out: Any, *,
                 transport_factory: Callable[[str], Any] = HidrawTransport,
                 find_control: Callable[[], str | None] = find_control_hidraw,
                 find_vendor: Callable[[], str | None] = find_vendor_hidraw,
                 hidraw_reader: Callable[..., Any] = HidrawReader,
                 openrazer_loaded: Callable[[], bool] = openrazer_loaded,
                 retry_delay: float = 0.2, backup_path: Path | None = None) -> None:
        self.out = out
        self._transport_factory = transport_factory
        self._find_control = find_control
        self._find_vendor = find_vendor
        self._hidraw_reader = hidraw_reader
        self._retry_delay = retry_delay
        self.mapper = Mapper(default_profile(), {}, out, on_dpi_stage=self._on_dpi_stage)
        self.router = InputRouter(self.mapper, out)
        self.service = NagaService(store, self.mapper, self,
                                   apply_device_settings=apply_device_settings,
                                   openrazer_loaded=openrazer_loaded,
                                   backup_path=backup_path)
        self._tasks: set[asyncio.Task] = set()
        self._mouse_path: str | None = None
        self._transport: Any = None
        self._naga: NagaV2Pro | None = None
        self._vendor: Any = None
        self._hw = dict(_DISCONNECTED)

    # -- Hardware protocol for the D-Bus service ---------------------------

    def hw_status(self) -> dict[str, Any]:
        return dict(self._hw)

    def scroll_profile_supported(self) -> bool:
        if self._naga is None:
            return False
        return _safe(self._naga.scroll_profile, None) is not None

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        await self.service.reload()

    def on_added(self, source: str, dev: Any) -> None:
        self.router.add(source, dev)
        if source == "mouse" and dev.path in self.router.paths:
            self._mouse_path = dev.path
            self._spawn(self._connect(dev.info.product))

    def on_removed(self, path: str) -> None:
        self._spawn(self._removed(path))

    async def settle(self) -> None:
        """Wait for all background work (hotplug handling) to finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    async def shutdown(self) -> None:
        await self.settle()
        await self._disconnect(reset_mode=True)
        await self.router.close()
        self.out.close()

    # -- internals -------------------------------------------------------------

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        async def guarded() -> None:
            try:
                await coro
            except Exception:
                logger.exception("Hintergrundaufgabe fehlgeschlagen")

        task = asyncio.get_running_loop().create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_dpi_stage(self, direction: str) -> None:
        # TODO(Task 7): switch the mouse's DPI stage up/down.
        logger.info("DPI-Stufe %s angefordert (noch nicht umgesetzt)", direction)

    async def _removed(self, path: str) -> None:
        await self.router.remove(path)
        if path == self._mouse_path:
            self._mouse_path = None
            await self._disconnect(reset_mode=False)  # unplug resets the mode itself

    async def _connect(self, product: int) -> None:
        await self._disconnect(reset_mode=False)
        path = None
        for _ in range(10):  # hidraw nodes may appear after the input nodes
            path = self._find_control()
            if path:
                break
            await asyncio.sleep(self._retry_delay)
        if not path:
            logger.warning("Steuerkanal (hidraw) der Maus nicht gefunden")
            return
        try:
            self._transport = self._transport_factory(path)
        except OSError as e:
            logger.warning("Kann %s nicht öffnen: %s", path, e)
            return
        naga = self._naga = NagaV2Pro(self._transport)
        try:
            naga.set_device_mode(DRIVER_MODE)
        except _HW_ERRORS as e:
            logger.warning("Treibermodus nicht gesetzt, obere Tasten bleiben ab Werk: %s", e)
        vendor_path = self._find_vendor()
        if vendor_path:
            reader = self._hidraw_reader(vendor_path, self.router.vendor_report)
            try:
                reader.start()
                self._vendor = reader
            except OSError as e:
                logger.warning("Kann %s nicht lesen: %s", vendor_path, e)
        self._hw = {"connected": True, "connection": CONNECTION.get(product, ""),
                    "battery": _safe(naga.battery, -1),
                    "charging": bool(_safe(naga.charging, False))}
        logger.info("Maus verbunden: %s", self._hw)
        self.service.notify_status()

    async def _disconnect(self, reset_mode: bool) -> None:
        if self._vendor is not None:
            await self._vendor.close()
            self._vendor = None
        if self._naga is not None and reset_mode:
            try:
                self._naga.set_device_mode(NORMAL_MODE)
            except _HW_ERRORS as e:
                logger.warning("Normalmodus nicht zurückgesetzt: %s", e)
        if self._transport is not None:
            close = getattr(self._transport, "close", None)
            if close:
                close()
        was_connected = self._hw["connected"]
        self._transport = self._naga = None
        self._hw = dict(_DISCONNECTED)
        if was_connected:
            self.service.notify_status()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="naga-configd",
                                 description="naga-config Dienst (Razer Naga V2 Pro)")
    ap.add_argument("--debug", action="store_true", help="ausführliches Protokoll")
    ap.add_argument("--config-dir", type=Path, default=None,
                    help="Konfigurationsordner (Standard: ~/.config/naga-config)")
    return ap


async def run(config_dir: Path) -> int:
    from dbus_next import BusType
    from dbus_next.aio import MessageBus
    from dbus_next.constants import NameFlag, RequestNameReply

    from .uinput_out import UinputOutput

    try:
        out = UinputOutput()
    except OSError as e:
        logger.error("Kann /dev/uinput nicht öffnen (udev-Regel installiert?): %s", e)
        return 1
    daemon = Daemon(ConfigStore(config_dir), out)
    bus = None
    watcher = DeviceWatcher(daemon.on_added, daemon.on_removed)
    try:
        await daemon.start()
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
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
        logger.info("naga-configd läuft")
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
    return asyncio.run(run(args.config_dir or default_config_dir()))


if __name__ == "__main__":
    sys.exit(main())
