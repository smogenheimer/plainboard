"""What a person's projects may use, set per person by an admin (Settings > Users).

A project is its creator's (``Project.owner_id``). What that person may use holds for the project,
whoever works in it and whoever changes its settings: the subscriptions its agents run on, the runners
it may be moved to, how many of its members work at once (across all of that person's projects), and
how many projects the person may create. Unset means no limit, which is what everyone had before.
A project of an admin, or of nobody (its creator was deleted): everything.

What the installation holds for itself (the global secrets, its GitHub token and SSH key, the global MCP
servers) a project gets only if its owner may use it (``User.may_use_shared``): ``shared``. Everything
that hands credentials or secrets to a project's runs, jobs and git work takes them from here.

The project's own limit to some subscriptions (``Project.account_ids``, set by an admin) comes on top.
Subscriptions people added themselves (``Account.owner_id``) are their own and whom they share them with.
Besides, whoever works in a project (a member, not a viewer) can give it their own subscriptions
(``Account.project_ids``, #93): its runs use them, whatever they are shared with, as long as that person
works in it and the project's owner has not switched them off (``Project.blocked_account_ids``).
An admin can stop people from adding their own (setting ``own_accounts``) and switch those they have off
for the whole installation (``own_accounts_off``, ``switched_off``): nothing runs on them until it is on again.
"""

from __future__ import annotations

from typing import Any

from plainboard_protocol import Credentials
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Account, Project, ProjectMember, ProjectRole, Runner, Secret, User
from ..security import decrypt
from . import secrets as secret_service
from . import settings as settings_service
from .errors import NotFound, ServiceError

BUILTIN = 0  # the built-in runner in ``User.runner_ids``
FIELDS = ("max_projects", "account_ids", "runner_ids", "max_parallel")


def limited(user: User | None) -> bool:
    return user is not None and not user.is_admin


async def owner(session: AsyncSession, project: Project) -> User | None:
    """The person whose limits hold for the project; None: nobody's (an admin's): everything."""
    user = await session.get(User, project.owner_id) if project.owner_id else None
    return user if limited(user) else None


SHARES = ("private", "people", "everyone")


def may_use(user: User | None, account: Account) -> bool:
    """May this person's projects run on the account? ``user`` None: a project of nobody.
    The installation's: as an admin allowed the person (``User.account_ids``). A person's own: its
    owner, and whom the owner shares it with."""
    if account.owner_id is None:
        return not limited(user) or user.account_ids is None or account.id in user.account_ids
    if user is not None and account.owner_id == user.id:
        return True
    return account.share == "everyone" or (account.share == "people" and user is not None and user.id in (account.shared_with or []))


def may_manage(user: User | None, account: Account) -> bool:
    """Change, delete, test and share it: its owner and admins (the installation's: admins only)."""
    return user is not None and (user.is_admin or (account.owner_id is not None and account.owner_id == user.id))


async def own_allowed(session: AsyncSession) -> bool:
    """May people other than admins add subscriptions of their own (#138)?"""
    return bool(await settings_service.get(session, "own_accounts"))


async def switched_off(session: AsyncSession) -> set[int]:
    """People's own subscriptions, if an admin switched them off with adding new ones: none of them is used."""
    values = await settings_service.get_all(session)
    if values["own_accounts"] or not values["own_accounts_off"]:
        return set()
    return set(await session.scalars(select(Account.id).where(Account.owner_id.is_not(None))))


async def usable(session: AsyncSession, user: User | None) -> set[int]:
    off = await switched_off(session)
    return {a.id for a in await session.scalars(select(Account)) if may_use(user, a) and a.id not in off}


async def given(session: AsyncSession, project: Project) -> list[Account]:
    """The subscriptions people gave the project themselves (``Account.project_ids``), switched off or not."""
    return [a for a in await session.scalars(select(Account).where(Account.owner_id.is_not(None))) if project.id in (a.project_ids or [])]


async def contributed(session: AsyncSession, project: Project) -> set[int]:
    """Of those, the ones the project uses: their owner works in it (an enabled member, not a viewer, or
    an admin), and its owner has not switched them off."""
    workers = select(ProjectMember.user_id).where(ProjectMember.project_id == project.id, ProjectMember.role != ProjectRole.VIEWER)
    people = set(await session.scalars(select(User.id).where(User.enabled, or_(User.is_admin, User.id.in_(workers)))))
    blocked = set(project.blocked_account_ids or [])
    return {a.id for a in await given(session, project) if a.owner_id in people and a.id not in blocked}


async def account_ids(session: AsyncSession, project: Project | None) -> set[int] | None:
    """The only accounts the project's agents may run on: what its owner may use, and of those the
    ones an admin limited the project to; and what its members gave it (``contributed``). None: no project, any."""
    if project is None:
        return None
    allowed = await usable(session, await session.get(User, project.owner_id) if project.owner_id else None)
    if project.account_ids:
        allowed &= set(project.account_ids)
    return (allowed | await contributed(session, project)) - set(project.blocked_account_ids or []) - await switched_off(session)


async def check_account(session: AsyncSession, project: Project | None, account_id: int | None, user: User | None = None) -> None:
    """``user``: the owner of a project that is not created yet."""
    if account_id is None:
        return
    allowed = await account_ids(session, project) if project is not None else await usable(session, user)
    if account_id not in allowed:
        raise ServiceError("This project may not use that subscription.")


def may_run_on(user: User | None, runner_id: int | None) -> bool:
    return not limited(user) or user.runner_ids is None or (runner_id or BUILTIN) in user.runner_ids


async def check_runner(session: AsyncSession, project: Project, runner_id: int | None) -> None:
    if not may_run_on(await owner(session, project), runner_id):
        raise ServiceError("This project may not run on that runner.", 403)


async def first_runner(session: AsyncSession, user: User | None) -> int | None:
    """Where a new project of this person starts: the built-in runner, unless only others are allowed."""
    if may_run_on(user, None):
        return None
    ids = [i for i in user.runner_ids or [] if i != BUILTIN]
    found = await session.scalar(select(func.min(Runner.id)).where(Runner.id.in_(ids))) if ids else None
    if found is None:
        raise ServiceError("No runner is allowed for your projects. Ask an admin.", 403)
    return found


async def start_runner(session: AsyncSession, user: User | None, runner_id: int | None, chosen: bool) -> int | None:
    """The runner a new project starts on: the one chosen (None: the built-in one), checked, else ``first_runner``."""
    if not chosen:
        return await first_runner(session, user)
    if runner_id is not None and await session.get(Runner, runner_id) is None:
        raise NotFound("Runner not found")
    if not may_run_on(user, runner_id):
        raise ServiceError("Your projects may not run on that runner.", 403)
    return runner_id


async def check_create(session: AsyncSession, user: User) -> None:
    """Before anything of a new project exists: may this person have one more?"""
    if not limited(user):
        return
    if not user.may_create_projects:
        raise ServiceError("You may not create projects here. Ask an admin.", 403)
    await first_runner(session, user)
    if user.max_projects is not None:
        own = int(await session.scalar(select(func.count(Project.id)).where(Project.owner_id == user.id)) or 0)
        if own >= user.max_projects:
            raise ServiceError(f"You have {own} projects, as many as you may have. Ask an admin for more, or delete one.", 403)


INSTALLATION_ONLY = ("github_token", "git_ssh_key")  # settings a project without ``shared`` does not get
NO_GITHUB_TOKEN = "This project has no GitHub token of its own (the installation's is not for it): add a GitHub token in the project settings."


def may_use_shared(user: User | None) -> bool:
    return not limited(user) or user.may_use_shared


async def shared(session: AsyncSession, project: Project) -> bool:
    """May the project use what the installation holds for itself (global secrets, its GitHub token and SSH key)?"""
    return may_use_shared(await owner(session, project))


async def secrets(session: AsyncSession, project: Project) -> list[Secret]:
    """The secrets the project's agents get: its own, and the global ones if it may use them."""
    return await secret_service.effective(session, project.id, shared=await shared(session, project))


async def credentials(session: AsyncSession, project: Project, values: dict[str, Any] | None = None, account: dict[str, Any] | None = None) -> Credentials:
    """Everything a run, job or git operation of the project gets: its own GitHub token (else the installation's,
    if it may use it), the secrets it may use (``Credentials.secrets``), and ``account`` (``accounts.fields``)."""
    values = values if values is not None else await settings_service.get_all(session)
    share = await shared(session, project)
    if not share:
        values = {k: v for k, v in values.items() if k not in INSTALLATION_ONLY}
    env = secret_service.env(await secret_service.effective(session, project.id, shared=share))
    return settings_service.credentials(values, decrypt(project.github_token_enc), env, account)


def github_hint(credentials: Credentials) -> str:
    """Said where git or GitHub work of a project failed that has no GitHub token to use."""
    return "" if credentials.github_token else f" {NO_GITHUB_TOKEN}"


async def max_parallel(session: AsyncSession, project: Project) -> int | None:
    user = await owner(session, project)
    return user.max_parallel if user is not None else None


async def caps(session: AsyncSession) -> tuple[dict[int, int], dict[int, int]]:
    """For the dispatcher: project -> its owner, for owners with a limit of members at work; owner -> that limit."""
    rows = (await session.execute(select(Project.id, User.id, User.max_parallel).join(User, User.id == Project.owner_id).where(User.max_parallel.is_not(None), ~User.is_admin))).all()
    return {pid: uid for pid, uid, _ in rows}, {uid: limit for _, uid, limit in rows}


async def clean(session: AsyncSession, values: dict) -> dict:
    """An admin's change of a person's limits (``FIELDS``), checked: the subscriptions and runners named exist."""
    out = {key: values[key] for key in FIELDS if key in values}
    for key, table, extra in (("account_ids", Account, set()), ("runner_ids", Runner, {BUILTIN})):
        if out.get(key) is None:
            continue
        ids = list(dict.fromkeys(out[key]))
        known = set(await session.scalars(select(table.id).where(table.id.in_(ids)))) | extra
        if unknown := [i for i in ids if i not in known]:
            raise NotFound(f"Not found: {unknown[0]}")
        out[key] = ids
    return out
