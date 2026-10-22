"""Macro player: one asyncio task per running macro, keyed by the trigger button."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from razertux.config.model import ButtonStep, DelayStep, KeyStep, Macro, MacroMode
from razertux.engine.output import Output
from razertux.hw.buttons import ButtonId

logger = logging.getLogger(__name__)

MIN_LOOP_DELAY = 0.001  # seconds; keeps empty/delay-free loops from spinning


class _Run:
    def __init__(self, mode: MacroMode) -> None:
        self.mode = mode
        self.pressed: list[int] = []  # codes this macro holds down
        self.task: asyncio.Task | None = None
        self.started = False       # first step has begun
        self.release_pending = False  # button released before start


class MacroPlayer:
    def __init__(self, out: Output,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._out = out
        self._sleep = sleep
        self._runs: dict[ButtonId, _Run] = {}

    def start(self, macro: Macro, mode: MacroMode, count: int, key: ButtonId) -> None:
        run = self._runs.get(key)
        if run is not None:
            if run.mode == "toggle":
                self._stop(key)
            return  # still running: ignore the repeated press
        run = _Run(mode)
        self._runs[key] = run
        run.task = asyncio.get_running_loop().create_task(
            self._run(key, run, macro, mode, count))

    def button_up(self, key: ButtonId) -> None:
        run = self._runs.get(key)
        if run is not None and run.mode == "while_held":
            if run.started:
                self._stop(key)
            else:  # quick tap: play one full iteration, then stop
                run.release_pending = True

    def stop_all_nowait(self) -> list[asyncio.Task]:
        """Stop every macro synchronously; returns the cancelled tasks to join."""
        tasks = []
        for key in list(self._runs):
            task = self._runs[key].task
            self._stop(key)
            if task is not None:
                tasks.append(task)
        return tasks

    async def stop_all(self) -> None:
        tasks = self.stop_all_nowait()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- internals ---------------------------------------------------------

    def _stop(self, key: ButtonId) -> None:
        run = self._runs.pop(key)
        if run.task is not None:
            run.task.cancel()
        self._release(run)

    def _release(self, run: _Run) -> None:
        if not run.pressed:
            return
        try:
            for code in reversed(run.pressed):
                self._out.key(code, 0)
            self._out.sync()
        except Exception:
            logger.exception("Fehler beim Loslassen von Makro-Tasten")
        run.pressed.clear()

    async def _run(self, key: ButtonId, run: _Run, macro: Macro,
                   mode: MacroMode, count: int) -> None:
        try:
            if mode == "repeat_n":
                for i in range(max(count, 0)):
                    if i:
                        await self._sleep(MIN_LOOP_DELAY)
                    await self._play(run, macro)
            elif mode == "while_held" or mode == "toggle":
                while True:
                    await self._play(run, macro)
                    if run.release_pending:
                        break
                    await self._sleep(MIN_LOOP_DELAY)
            else:
                await self._play(run, macro)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Makro %r ist fehlgeschlagen", macro.id)
        finally:
            if self._runs.get(key) is run:
                del self._runs[key]
                self._release(run)

    async def _play(self, run: _Run, macro: Macro) -> None:
        run.started = True
        for step in macro.steps:
            if isinstance(step, DelayStep):
                await self._sleep(step.ms / 1000)
            elif isinstance(step, (KeyStep, ButtonStep)):
                if step.down:
                    run.pressed.append(step.code)
                elif step.code in run.pressed:
                    run.pressed.remove(step.code)
                self._out.key(step.code, 1 if step.down else 0)
                self._out.sync()
            await asyncio.sleep(0)  # always yield: keeps loops cancellable
        await asyncio.sleep(0)
