"""BatteryMonitor: polling only when wireless, change callback, one-shot warning."""
import asyncio

from razertux.daemon.battery import BatteryMonitor, notify_desktop


class FakeDev:
    def __init__(self, level=80, charging=False):
        self.level, self.is_charging = level, charging
        self.reads = 0

    def battery(self):
        self.reads += 1
        return self.level

    def charging(self):
        return self.is_charging


def make(level=80, charging=False, connection="wireless", threshold=15):
    dev = FakeDev(level, charging)
    changes, notes = [], []
    mon = BatteryMonitor(dev, interval_s=60, on_change=lambda b, c: changes.append((b, c)),
                         connection=lambda: connection, threshold=lambda: threshold,
                         notify=notes.append)
    return mon, dev, changes, notes


def feed(mon, dev, *levels, charging=False):
    for lv in levels:
        dev.level, dev.is_charging = lv, charging
        mon.poll()


def test_warns_once_below_threshold_and_rearms_after_charging():
    mon, dev, _, notes = make()
    feed(mon, dev, 14)
    assert notes == [14]
    feed(mon, dev, 13, 12)
    assert notes == [14]  # no repeat
    feed(mon, dev, 25, charging=True)
    feed(mon, dev, 14)
    assert notes == [14, 14]


def test_rearm_needs_threshold_plus_five():
    mon, dev, _, notes = make()
    feed(mon, dev, 14, 19, 14)
    assert notes == [14]
    feed(mon, dev, 20, 14)
    assert notes == [14, 14]


def test_no_warning_while_charging_or_when_disabled():
    mon, dev, _, notes = make()
    feed(mon, dev, 10, charging=True)
    assert notes == []
    mon, dev, _, notes = make(threshold=0)
    feed(mon, dev, 3)
    assert notes == []


def test_wired_does_not_query():
    mon, dev, changes, notes = make(level=5, connection="wired")
    mon.poll()
    assert dev.reads == 0 and changes == [] and notes == []


def test_on_change_only_when_value_changes():
    mon, dev, changes, _ = make(level=80)
    feed(mon, dev, 80, 80, 79)
    feed(mon, dev, 79, charging=True)
    assert changes == [(80, False), (79, False), (79, True)]


def test_read_errors_are_ignored():
    mon, dev, changes, _ = make()

    def boom():
        raise OSError("weg")
    dev.battery = boom
    mon.poll()  # must not raise
    assert changes == []


async def test_run_polls_every_interval_until_stopped():
    mon, dev, _, _ = make()
    mon.interval_s = 0.01
    mon.start()
    await asyncio.sleep(0.05)
    await mon.stop()
    n = dev.reads
    assert n >= 2
    await asyncio.sleep(0.03)
    assert dev.reads == n


async def test_notify_desktop_calls_freedesktop_notifications():
    sent = []

    class Bus:
        async def call(self, msg):
            sent.append(msg)

    await notify_desktop(Bus(), "Titel", "Text")
    (msg,) = sent
    assert msg.destination == "org.freedesktop.Notifications"
    assert msg.member == "Notify" and msg.signature == "susssasa{sv}i"
    assert msg.body[3:5] == ["Titel", "Text"]


def test_warned_state_survives_new_monitor():
    from razertux.daemon.battery import WarnState
    state = WarnState()
    notes = []
    for _ in range(2):  # dongle reconnect: new monitor, same state
        dev = FakeDev(10)
        BatteryMonitor(dev, on_change=None, state=state, notify=notes.append).poll()
    assert notes == [10]


async def test_run_survives_callback_errors_and_stop_never_raises(caplog):
    mon, dev, _, _ = make()
    mon.interval_s = 0.01
    calls = []

    def bad(b, c):
        calls.append(b)
        dev.level -= 1  # new value each poll -> callback every time
        raise RuntimeError("kaputt")
    mon._on_change = bad
    mon.start()
    await asyncio.sleep(0.05)
    assert len(calls) >= 2  # loop kept running
    await mon.stop()
    assert "kaputt" in caplog.text


async def test_stop_logs_unexpected_task_error(caplog):
    mon, *_ = make()

    async def boom():
        raise RuntimeError("weg")
    mon._task = asyncio.get_running_loop().create_task(boom())
    await asyncio.sleep(0)
    await mon.stop()  # must not raise
    assert "weg" in caplog.text


def test_on_poll_reports_each_reading_ok_or_failed():
    dev = FakeDev()
    seen = []
    mon = BatteryMonitor(dev, on_poll=seen.append)
    mon.poll()
    ok_battery = dev.battery

    def boom():
        raise OSError("schläft")
    dev.battery = boom
    mon.poll()
    dev.battery = ok_battery
    mon.poll()
    assert seen == [True, False, True]
    BatteryMonitor(dev, on_poll=seen.append, connection=lambda: "wired").poll()
    assert len(seen) == 3  # wired: no reading, no callback
