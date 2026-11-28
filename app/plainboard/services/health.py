"""Why nothing runs (#22): every state that keeps PlainBoard from working, in one place.

Nothing of it is new: everything is paused, a project is paused (by a person or before its limits), its
runner is away or updates itself, its manager is off, the manager is not woken by reports, no member may
work at once (limit 0), members were paused, a subscription is not signed in or used up. But each lives
somewhere else, and a turn that waits says nothing. So whoever looks from outside (a person, an MCP
client, an agent) wondered why an enabled manager does not start. This reads all of it and names it.

Read from the database and the runners' connections; it decides nothing and starts nothing. Issues carry a
code and their values, not text: the UI translates them, ``describe`` writes them for agents and MCP.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Account, Agent, Project, Task, TaskStatus, User
from ..schemas import HealthIssue, HealthOut
from . import accounts as account_service
from . import limits as limits_service
from . import pause as pause_service
from . import projects as project_service

OPEN = (TaskStatus.TODO, TaskStatus.IN_PROGRESS)


def _since(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _at(unix: int | None) -> datetime | None:
    return datetime.fromtimestamp(unix, UTC).replace(tzinfo=None) if unix else None


async def everything(session: AsyncSession) -> HealthIssue | None:
    """Everything is paused (services/pause.py)."""
    record = await pause_service.state(session)
    if record is None:
        return None
    return HealthIssue(code="paused_everything", blocking=True, by=record.get("by", ""), since=_since(record.get("at")))


async def of_project(session: AsyncSession, runners, project: Project) -> list[HealthIssue]:
    """What keeps this project from working, the gravest first. Without "everything is paused"."""
    found: list[HealthIssue] = []
    where = {"project_id": project.id, "project": project.name}
    if project.paused_at is not None:
        found.append(HealthIssue(code="project_paused", blocking=True, by=project.paused_by, since=project.paused_at, **where))
    if project.limit_paused_at is not None:
        found.append(HealthIssue(code="limit_paused", blocking=True, since=project.limit_paused_at, until=project.limit_resumes_at, **where))
    if runners is not None:
        if not runners.ready(project.id):
            found.append(HealthIssue(code="runner_away", blocking=True, **where))
        elif runners.updating_for(project.id):
            found.append(HealthIssue(code="runner_updating", blocking=True, **where))
    manager = await session.scalar(select(Agent).where(Agent.project_id == project.id, Agent.is_manager))
    open_tickets = await session.scalar(select(func.count(Task.id)).where(Task.project_id == project.id, Task.status.in_(OPEN), Task.archived_at.is_(None))) or 0
    if manager is not None and not manager.enabled:
        found.append(HealthIssue(code="manager_off", blocking=True, agent=manager.name, count=open_tickets, **where))
    if await project_service.parallel_limit(session, project) == 0:
        found.append(HealthIssue(code="parallel_zero", blocking=True, **where))
    if not project.auto_wake_manager:
        found.append(HealthIssue(code="auto_wake_off", blocking=False, **where))
    if project.paused_at is None:  # a person's pause pauses the team as well: that says enough
        paused = list(await session.scalars(select(Agent.name).where(Agent.project_id == project.id, Agent.team_pause, ~Agent.is_manager).order_by(Agent.name)))
        if paused:
            found.append(HealthIssue(code="team_paused", blocking=False, count=len(paused), detail=", ".join(paused), **where))
    return found


async def of_accounts(session: AsyncSession, accounts: list[Account]) -> list[HealthIssue]:
    """Subscriptions that are switched on but sign in nowhere, or whose whole plan is used up."""
    found: list[HealthIssue] = []
    windows = await limits_service.windows(session)
    for account in accounts:
        if not account.enabled:
            continue
        if account_service.unusable(account, None) == "not_signed_in":
            found.append(HealthIssue(code="account_signed_out", blocking=False, account=account.label))
            continue
        full = [w for w in windows.get(account.key, []) if w.model is None and (w.status == "rejected" or w.utilization >= 1)]
        if full:
            resets = [w.resets_at for w in full if w.resets_at]
            found.append(HealthIssue(code="account_used_up", blocking=False, account=account.label, until=_at(max(resets)) if resets else None))
    return found


async def check(session: AsyncSession, runners, user: User | None, project_ids: list[int] | None = None) -> HealthOut:
    """Everything that is not as it should be, for what ``user`` sees (None: a read key, all of it).
    ``project_ids``: only these projects (default: all the user has a part in)."""
    from . import users as user_service

    issues: list[HealthIssue] = []
    if found := await everything(session):
        issues.append(found)
    seen = None if user is None else set(await user_service.roles(session, user))
    query = select(Project).order_by(Project.name)
    if project_ids is not None:
        query = query.where(Project.id.in_(project_ids))
    for project in await session.scalars(query):
        if seen is None or project.id in seen:
            issues += await of_project(session, runners, project)
    accounts = await account_service.list_accounts(session) if user is None else await account_service.visible(session, user)
    issues += await of_accounts(session, accounts)
    return HealthOut(ok=not any(i.blocking for i in issues), issues=issues, checked_at=datetime.now(UTC).replace(tzinfo=None))


async def hold(session: AsyncSession, runners, agent: Agent) -> HealthIssue | None:
    """Why a queued turn of this agent does not start, if something holds it: everything or its project is
    paused, its runner is away, or the agent itself is off. None: nothing of that (it waits for its turn)."""
    if found := await everything(session):
        return found
    project = await session.get(Project, agent.project_id)
    if project is None:
        return None
    for issue in await of_project(session, runners, project):
        if issue.code in ("project_paused", "runner_away", "runner_updating"):
            return issue
    if not agent.enabled:
        return HealthIssue(code="agent_off", blocking=True, agent=agent.name, project_id=project.id, project=project.name)
    return None


def _when(value: datetime | None) -> str:
    return f"{value:%Y-%m-%d %H:%M} UTC" if value else ""


def describe(issue: HealthIssue) -> str:
    """One line for agents and MCP clients (the UI has its own words)."""
    where = f"Project {issue.project}: " if issue.project else ""
    by = f" by {issue.by}" if issue.by else ""
    since = f" since {_when(issue.since)}" if issue.since else ""
    until = f" until {_when(issue.until)}" if issue.until else ""
    text = {
        "paused_everything": f"Everything is paused{by}{since}: no turn starts until an admin resumes (Settings, Pause everything).",
        "project_paused": f"paused{by}{since}: no turn starts until a person resumes it.",
        "limit_paused": f"paused before its subscriptions run out{since}{until}; chats still start.",
        "runner_away": "its runner is not connected: turns wait in the queue.",
        "runner_updating": "its runner updates itself: new turns wait until it is back.",
        "manager_off": f"the manager {issue.agent} is switched off ({issue.count} open tickets): it starts no turn.",
        "parallel_zero": "parallel agents is 0: no member starts work (the manager still does).",
        "auto_wake_off": "auto-wake is off: reports and answers do not start the manager, only a chat does.",
        "team_paused": f"{issue.count} members are paused: {issue.detail}.",
        "agent_off": f"{issue.agent} is switched off.",
        "account_signed_out": f"Subscription {issue.account} is not signed in.",
        "account_used_up": f"Subscription {issue.account} is used up{until}.",
    }.get(issue.code, issue.code)
    return where + text


def lines(out: HealthOut) -> str:
    """The whole check as short lines; one line when all is well."""
    if not out.issues:
        return "All is well: nothing is paused, every runner is connected, every subscription has room."
    return "\n".join(("- " if not i.blocking else "! ") + describe(i) for i in out.issues)
