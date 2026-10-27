"""PauseDevice/ResumeDevice over a real private bus (dbus-daemon): sender
attribution, a wrong-path call not poisoning it, caller exit -> auto-resume."""
import asyncio
import os
import shutil
import subprocess

import pytest
from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus
from test_dbus_service import DA, NAGA, grabbed, make

from razertux.daemon.dbus_service import INTERFACE, OBJECT_PATH

pytestmark = pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="no dbus-daemon")


@pytest.fixture
def private_bus():
    p = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                         stdout=subprocess.PIPE, text=True)
    addr = p.stdout.readline().strip()
    yield addr
    p.terminate()
    p.wait()
    p.stdout.close()


async def close(bus):
    bus.disconnect()
    await bus.wait_for_disconnect()
    bus._stream.close()  # dbus-next leaves these to the GC (ResourceWarning)
    bus._sock.close()


def invoke(bus, member, model, path=OBJECT_PATH):
    return bus.call(Message(destination="x.razertux.test", path=path, interface=INTERFACE,
                            member=member, signature="s", body=[model]))


async def test_pause_over_bus(tmp_path, private_bus):
    d, svc, *_ = make(tmp_path)
    srv = await MessageBus(bus_address=private_bus).connect()
    srv.export(OBJECT_PATH, svc)
    await svc.attach(srv)
    await srv.request_name("x.razertux.test")
    c1 = await MessageBus(bus_address=private_bus).connect()
    c2 = await MessageBus(bus_address=private_bus).connect()

    bad = await invoke(c1, "PauseDevice", NAGA, path="/other")  # error, must leave no trace
    assert bad.message_type == MessageType.ERROR
    assert (await invoke(c2, "PauseDevice", NAGA)).message_type == MessageType.METHOD_RETURN
    assert not grabbed(d) and svc._pauses[NAGA][0] == {c2.unique_name}

    unknown = await invoke(c2, "PauseDevice", "nope")
    assert unknown.message_type == MessageType.ERROR and unknown.error_name.endswith("UnknownDevice")

    await close(c1)  # not the pauser: stays paused
    await asyncio.sleep(0.2)
    assert not grabbed(d)
    await close(c2)  # the pauser leaves: mapping comes back
    for _ in range(50):
        if grabbed(d):
            break
        await asyncio.sleep(0.05)
    assert grabbed(d) and NAGA not in svc._pauses

    c3 = await MessageBus(bus_address=private_bus).connect()
    await invoke(c3, "PauseDevice", DA)
    assert (await invoke(c3, "ResumeDevice", DA)).message_type == MessageType.METHOD_RETURN
    assert DA not in svc._pauses
    await close(srv)
    await close(c3)
    svc.shutdown()
