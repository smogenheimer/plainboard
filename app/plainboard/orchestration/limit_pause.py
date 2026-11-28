"""Pauses a project before its subscriptions run out and resumes it when one has room again
(services/limit_pause.py). A watcher without a model: it reads the stored limits, a window that has
reset counts as empty from its reset time on, so a project goes on within one interval of it.
A plan topped up shows only in a new reading: ``recheck`` reads the limits at once, when a person
asks for it (the pause notice) or writes in a paused project's chat.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..services import limit_pause
from ..services import limits as limits_service
from ..models import Project
from .run_manager import RunManager

log = logging.getLogger(__name__)

RECHECK_SECONDS = 15.0  # a reading this new is read again by nobody who asks: the limits change no faster


class LimitPauseWatcher:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], runs: RunManager, interval: float):
        self._sm = sessionmaker
        self._runs = runs
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._read_at = 0.0
        self._soon: set[asyncio.Task[None]] = set()

    async def recheck(self) -> None:
        """Read the limits now and pause or resume the projects by them, without waiting for the next reading."""
        async with self._lock:
            if time.monotonic() - self._read_at >= RECHECK_SECONDS:
                try:
                    await self._runs.read_limits_now()
                except Exception:
                    log.exception("Limit pause recheck: the limits were not read, the stored ones count")
                self._read_at = time.monotonic()
            await self.tick()

    def recheck_soon(self) -> None:
        """``recheck`` in the background (a chat message is not held up by it)."""

        async def go() -> None:
            try:
                await self.recheck()
            except Exception:
                log.exception("Limit pause recheck failed")

        task = asyncio.create_task(go(), name="limit-pause-recheck")
        self._soon.add(task)
        task.add_done_callback(self._soon.discard)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="limit-pause-watcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)  # its database connection is back before the engine goes
        for task in list(self._soon):
            task.cancel()
        await asyncio.gather(*self._soon, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.tick()
            except Exception:
                log.exception("Limit pause watcher tick failed")

    async def tick(self) -> tuple[int, int]:
        """Returns how many projects were paused and resumed."""
        paused = resumed = 0
        async with self._sm() as s:
            found = await limits_service.windows(s)
            for project in list(await s.scalars(select(Project).order_by(Project.id))):
                near, until = await limit_pause.nearly_used_up(s, project, found)
                if near and project.limit_paused_at is None:
                    await self._runs.pause_for_limits(s, project, until)
                    paused += 1
                elif not near and project.limit_paused_at is not None:
                    await self._runs.resume_after_limits(s, project)
                    resumed += 1
                elif near and project.limit_resumes_at != until:
                    await self._runs.limit_resumes_at(s, project, until)
        return paused, resumed
