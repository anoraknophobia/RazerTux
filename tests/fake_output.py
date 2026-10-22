"""Recording Output + controllable sleep for engine tests."""
import asyncio


class RecordingOutput:
    def __init__(self):
        self.log = []      # (code, value) for key events
        self.rels = []     # (axis, value)
        self.events = []   # everything in order, incl. ("sync",)

    def key(self, code, value):
        self.log.append((code, int(value)))
        self.events.append(("key", code, int(value)))

    def rel(self, axis, value):
        self.rels.append((axis, value))
        self.events.append(("rel", axis, value))

    def sync(self):
        self.events.append(("sync",))


class FakeSleep:
    """Records requested delays; each sleep yields once to the loop."""

    def __init__(self):
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)
        await asyncio.sleep(0)


class GateSleep:
    """Sleep that blocks until release() is called (for mid-run stops)."""

    def __init__(self):
        self.calls = []
        self._gate = asyncio.Event()

    async def __call__(self, seconds):
        self.calls.append(seconds)
        await self._gate.wait()

    def release(self):
        self._gate.set()
