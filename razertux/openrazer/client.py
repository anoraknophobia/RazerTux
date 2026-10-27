"""Async client for the OpenRazer daemon (D-Bus name ``org.razer`` on the session bus).

Only used in "RazerTux + OpenRazer" mode (Settings.device_access == "openrazer").
Every call has a timeout, so a hung OpenRazer daemon never blocks razertuxd.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

OPENRAZER_BUS_NAME = "org.razer"
OPENRAZER_TIMEOUT = 2.0  # seconds per D-Bus call
ROOT_PATH = "/org/razer"


class OpenRazerError(Exception):
    """A call to the OpenRazer daemon failed (error reply, timeout, no bus)."""


class OpenRazerTimeout(OpenRazerError):
    """The OpenRazer daemon did not answer in time (probably hung)."""


class Bus(Protocol):
    async def call(self, path: str, interface: str, member: str, signature: str = "",
                   args: tuple = ()) -> list: ...

    async def introspect(self, path: str) -> dict[str, set[str]]: ...

    async def name_has_owner(self, name: str) -> bool: ...

    def on_owner_changed(self, name: str, cb: Callable[[bool], None]) -> None: ...


@dataclass(frozen=True)
class OrDevice:
    """One device object of the OpenRazer daemon."""
    serial: str
    path: str
    pid: int
    methods: dict[str, frozenset[str]]  # interface -> method names

    def has(self, interface: str, *members: str) -> bool:
        found = self.methods.get(interface, frozenset())
        return all(m in found for m in members)


async def _timed(coro: Any) -> Any:
    from . import client as _self  # read the module value (tests shorten it)
    try:
        return await asyncio.wait_for(coro, _self.OPENRAZER_TIMEOUT)
    except asyncio.TimeoutError as e:
        raise OpenRazerTimeout("timeout") from e


class SessionBus:
    """Bus on a dbus-next session bus connection (opened on first use)."""

    def __init__(self) -> None:
        self._bus: Any = None
        self._owner_cbs: dict[str, list[Callable[[bool], None]]] = {}

    async def _connect(self) -> Any:
        from dbus_next import BusType, Message
        from dbus_next.aio import MessageBus
        if self._bus is None or not self._bus.connected:
            self._bus = await _timed(MessageBus(bus_type=BusType.SESSION).connect())
            self._bus.add_message_handler(self._on_message)
            for name in self._owner_cbs:
                await self._add_match(Message, name)
        return self._bus

    async def _add_match(self, message: Any, name: str) -> None:
        rule = ("type='signal',sender='org.freedesktop.DBus',member='NameOwnerChanged',"
                f"arg0='{name}'")
        await _timed(self._bus.call(message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="AddMatch", signature="s", body=[rule])))

    def _on_message(self, msg: Any) -> None:
        if msg.member == "NameOwnerChanged" and msg.body and msg.body[0] in self._owner_cbs:
            for cb in self._owner_cbs[msg.body[0]]:
                cb(bool(msg.body[2]))

    async def _raw(self, destination: str, path: str, interface: str, member: str,
                   signature: str = "", args: tuple = ()) -> list:
        from dbus_next import Message, MessageType
        try:
            bus = await self._connect()
            reply = await _timed(bus.call(Message(
                destination=destination, path=path, interface=interface, member=member,
                signature=signature, body=list(args))))
        except OpenRazerError:
            raise
        except Exception as e:  # no bus, closed connection, bad arguments ...
            raise OpenRazerError(f"{type(e).__name__}: {e}") from e
        if reply.message_type == MessageType.ERROR:
            raise OpenRazerError(f"{reply.error_name}: {reply.body[0] if reply.body else ''}")
        return list(reply.body)

    async def call(self, path: str, interface: str, member: str, signature: str = "",
                   args: tuple = ()) -> list:
        return await self._raw(OPENRAZER_BUS_NAME, path, interface, member, signature, args)

    async def introspect(self, path: str) -> dict[str, set[str]]:
        from dbus_next.introspection import Node
        xml, = await self._raw(OPENRAZER_BUS_NAME, path, "org.freedesktop.DBus.Introspectable",
                               "Introspect")
        node = Node.parse(xml)
        return {i.name: {m.name for m in i.methods} for i in node.interfaces}

    async def name_has_owner(self, name: str) -> bool:
        try:
            res, = await self._raw("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                   "org.freedesktop.DBus", "NameHasOwner", "s", (name,))
        except OpenRazerError:
            return False
        return bool(res)

    def on_owner_changed(self, name: str, cb: Callable[[bool], None]) -> None:
        self._owner_cbs.setdefault(name, []).append(cb)
        if self._bus is not None and self._bus.connected:
            from dbus_next import Message
            asyncio.get_running_loop().create_task(self._add_match(Message, name))


class OpenRazerClient:
    def __init__(self, bus: Bus):
        self.bus = bus

    async def available(self) -> bool:
        return await self.bus.name_has_owner(OPENRAZER_BUS_NAME)

    def on_availability(self, cb: Callable[[bool], None]) -> None:
        self.bus.on_owner_changed(OPENRAZER_BUS_NAME, cb)

    async def devices(self) -> list[OrDevice]:
        """Every device the daemon manages; a device whose calls fail is skipped.
        Raises OpenRazerError if the list cannot be read or the daemon stops answering
        (no further calls after the first timeout)."""
        if not await self.available():
            return []
        serials, = await self.bus.call(ROOT_PATH, "razer.devices", "getDevices")
        found = []
        for serial in serials:
            path = f"{ROOT_PATH}/device/{serial}"
            try:
                vid_pid, = await self.bus.call(path, "razer.device.misc", "getVidPid")
                methods = await self.bus.introspect(path)
                dev = OrDevice(str(serial), path, int(vid_pid[1]),
                               {k: frozenset(v) for k, v in methods.items()})
            except OpenRazerTimeout:
                raise
            except Exception as e:  # error reply, malformed answer
                logger.warning("OpenRazer: device %s skipped: %s", path, e)
                continue
            found.append(dev)
        return found
