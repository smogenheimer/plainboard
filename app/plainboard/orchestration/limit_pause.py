"""Pauses a project before its subscriptions run out and resumes it when one has room again
(services/limit_pause.py). A watcher without a model: it reads the stored limits, a window that has
reset counts as empty from its reset time on, so a project goes on within one interval of it.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..services import limit_pause
from ..services import limits as limits_service
from ..models import Project
from .run_manager import RunManager

log = logging.getLogger(__name__)


class LimitPauseWatcher:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], runs: RunManager, interval: float):
        self._sm = sessionmaker
        self._runs = runs
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="limit-pause-watcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)  # its database connection is back before the engine goes

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
