"""A project pauses by itself before its subscriptions run out, and goes on by itself when one has room again.

When every subscription a project may run on is nearly used up (``accounts.SWITCH_AT``, the line from
which the choice of an account calls one nearly full), a new turn would only use up the rest and stop
half-way with the provider's limit message. So PlainBoard starts no new turn of it (a chat with the user
still starts), the turns that run wrap up at a safe point, and its open assignments stay open and wait
in the queue. As soon as one subscription has room again (its window reset, or a new reading says so),
it goes on. A project that may also use an API key or an endpoint has no plan limit and never pauses.
A project whose owner switched it off (``Project.limit_pause``) never pauses so.
This pause is PlainBoard's own (``Project.limit_paused_at``): a person's pause (``paused_at``) is not touched.
"""

from __future__ import annotations

from datetime import UTC, datetime

from plainboard_protocol import LimitWindow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, Project
from . import accounts as account_service
from . import limits as limits_service
from . import permissions

NEAR = account_service.SWITCH_AT


def _resets(windows: list[LimitWindow], models: set[str | None]) -> int | None:
    """When the windows that make the account (nearly) full have all reset; None if one does not say."""
    full = [w.resets_at for w in windows if (w.status == "rejected" or w.utilization >= NEAR) and any(limits_service.applies(w, m) for m in models)]
    return max(full) if full and all(full) else None  # type: ignore[type-var]


async def nearly_used_up(session: AsyncSession, project: Project, found: dict[str, list[LimitWindow]] | None = None) -> tuple[bool, datetime | None]:
    """Whether every account the project's agents may run on is a subscription that is nearly used up,
    and when the first of them is expected to have room again (naive UTC; None: unknown). Always False
    for a project that does not pause so."""
    if not project.limit_pause:
        return False, None
    agents = (await session.execute(select(Agent.provider, Agent.model).where(Agent.project_id == project.id))).all()
    providers = {provider for provider, _ in agents}
    models = {model for _, model in agents} or {None}  # a window for certain models (Fable) leaves the others room
    allowed = await permissions.account_ids(session, project)
    accounts = [a for a in await account_service.list_accounts(session) if a.enabled and a.provider in providers and (allowed is None or a.id in allowed)]
    if pinned := next((a for a in accounts if a.id == project.account_id), None):
        accounts = [pinned]
    if not accounts or not all(account_service.has_plan(a) for a in accounts):
        return False, None
    found = await limits_service.windows(session) if found is None else found
    if any(limits_service.fullest(found.get(a.key, []), m) < NEAR for a in accounts for m in models):
        return False, None
    ends = [end for a in accounts if (end := _resets(found.get(a.key, []), models))]
    return True, datetime.fromtimestamp(min(ends), UTC).replace(tzinfo=None) if ends else None
