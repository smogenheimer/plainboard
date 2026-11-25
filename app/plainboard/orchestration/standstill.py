"""Wakes a manager whose project stands still although work is ready (see services/standstill.py).

A watcher without a model: looking at the board costs nothing, only the turn it starts does, and it
starts one only for a situation the manager has not been told about yet.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Project, utcnow
from ..services import flow_log
from ..services import projects as project_service
from ..services import standstill as standstill_service
from . import inbox
from .run_manager import RunManager

log = logging.getLogger(__name__)


class StandstillWatcher:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], runs: RunManager, interval: float):
        self._sm = sessionmaker
        self._runs = runs
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        # project -> what its manager was told about last (the tickets, and whether the team rested),
        # and when. In memory: after a restart a manager may hear of the same once more.
        self._told: dict[int, tuple[tuple[frozenset[int], bool], datetime]] = {}

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="standstill-watcher")

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
                log.exception("Standstill watcher tick failed")

    async def tick(self, now: datetime | None = None) -> int:
        """Returns how many managers were woken."""
        if self._runs.paused:
            return 0
        now = now or utcnow()
        woken = 0
        async with self._sm() as s:
            for project in list(await s.scalars(select(Project).where(Project.paused_at.is_(None), Project.limit_paused_at.is_(None)).order_by(Project.id))):
                found = await standstill_service.find(s, project, await project_service.parallel_limit(s, project), self._runs.waiting_for_job(), now)
                if told := self._told.get(project.id):
                    # A ticket that is no longer ready (someone works on it, it waits for the user) is
                    # forgotten: should it stand ready again later, that is news.
                    still = found.ticket_ids if found else frozenset(t.id for t in await standstill_service.ready(s, project))
                    self._told[project.id] = ((told[0][0] & still, told[0][1]), told[1])
                if found is None:
                    continue
                told = self._told.get(project.id)
                if told and (not standstill_service.is_news(found, told[0]) or (now - told[1]).total_seconds() < standstill_service.RETELL_SECONDS):
                    continue
                rested = found.resting or bool(told and told[0][0] == found.ticket_ids and told[0][1])
                self._told[project.id] = ((found.ticket_ids, rested), now)
                await inbox.push(s, found.manager_id, standstill_service.text(found))
                await self._runs.request_wake(s, found.manager_id)
                await flow_log.record(s, project.id, "manager.told_of_standstill", agent_id=found.manager_id, tickets=[t.number for t in found.tickets], free=found.free, limit=found.limit, resting=found.resting, quiet_minutes=round(found.quiet_seconds / 60))
                woken += 1
        return woken
