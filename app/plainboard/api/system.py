"""Health, provider catalog and global settings."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import BaseModel

from .. import db_move
from ..config import get_config
from ..models import TaskStatus, utcnow
from ..orchestration.prompts import PINNED_BUDGET_CHARS
from ..schemas import AccessKeyOut, CheckOut, HealthOut, LimitsOut, PauseOut, PublicApiKeyIn, SearchModelIn, RetentionPreviewOut, SearchStatusOut, SettingsOut, SettingsUpdate, TokenUsageOut, UsageOut
from ..services import access_keys as access_key_service
from ..services import accounts as account_service
from ..services import db_read as db_read_service
from ..services import health as health_service
from ..services import limits as limits_service
from ..services import pause as pause_service
from ..services import retention as retention_service
from ..services import runners as runner_service
from ..services import search as search_service
from ..services import settings as settings_service
from ..services import stats as stats_service
from ..services import users as user_service
from ..services import usage as usage_service
from ..services import version as version_service
from ..services.errors import ServiceError
from ..orchestration.runner_client import RunnerError
from .deps import Runner, Runs, Session, Who

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


RUNNER_DOWN_AFTER_SECONDS = 30  # one slow answer of the runner is not "not reachable"
_runner_seen: dict[str, Any] = {"providers": [], "failing_since": None}


@router.get("/meta")
async def meta(runner: Runner, session: Session) -> dict[str, Any]:
    try:
        every = await runner.providers()
        _runner_seen.update(providers=every, failing_since=None)
        runner_ok = True
    except RunnerError:
        since = _runner_seen["failing_since"] = _runner_seen["failing_since"] or time.monotonic()
        runner_ok = time.monotonic() - since < RUNNER_DOWN_AFTER_SECONDS
        every = _runner_seen["providers"] if runner_ok else []
    providers = [p.model_dump() for p in await account_service.catalog(session, every)] if every else []
    return {
        "providers": providers,
        # Providers that are an endpoint, set up or not: what an account can be added for.
        "endpoints": [p.model_dump() for p in every if p.endpoint is not None],
        "runner_ok": runner_ok,
        "statuses": [s.value for s in TaskStatus],
        "pinned_note_budget_chars": PINNED_BUDGET_CHARS,
        # Semantic search can exist here (PostgreSQL with pgvector): the project settings show its switch.
        "semantic_search": await search_service.available(session),
        # The running commit and how far it is behind the newest PlainBoard; None: not known or not wanted.
        "version": version_service.current() if await settings_service.get(session, "update_check") else None,
        "database": "postgresql" if get_config().postgres else "sqlite",
        # The runner image tag that matches this PlainBoard: latest (release) or edge (:edge, built from source).
        "runner_tag": runner_service.tag(),
    }


RESTART_AFTER_SECONDS = 1.0  # the answer goes out first


@router.get("/database")
async def database() -> dict[str, Any]:
    """Which database, and with SQLite whether the move to the bundled PostgreSQL can be asked for here
    (`move`: ready, not_empty, unreachable, requested; None: no bundled one) and why the last one failed."""
    config = get_config()
    sqlite_file = config.data_dir / "plainboard.db"
    out: dict[str, Any] = {"database": "postgresql" if config.postgres else "sqlite", "auto": config.auto, "move": None, "failed": None}
    if not config.postgres:
        failed = db_move.failed(sqlite_file)
        out["failed"] = failed.read_text(encoding="utf-8").strip() if failed.exists() else None
        if config.auto:
            out["move"] = "requested" if db_move.requested(sqlite_file).exists() else await asyncio.to_thread(db_move.bundled_state)
    return out


@router.post("/database/move")
async def move_database() -> dict[str, Any]:
    """Move to the bundled PostgreSQL: asked for in a file, then PlainBoard ends and its container is
    started again (restart: unless-stopped), and the move runs at that start (migrate.upgrade)."""
    config = get_config()
    if config.postgres:
        raise ServiceError("PlainBoard runs on PostgreSQL already.", 409)
    if not config.auto:
        raise ServiceError("Only with the current docker-compose.yml (PLAINBOARD_DATABASE=auto and its db service).", 409)
    state = await asyncio.to_thread(db_move.bundled_state)
    if state != "ready":
        raise ServiceError(
            "The bundled PostgreSQL (service db) is not reachable." if state == "unreachable" else "The bundled PostgreSQL database holds data already.",
            409,
        )
    sqlite_file = config.data_dir / "plainboard.db"
    db_move.requested(sqlite_file).write_text("Asked for in Settings: move to PostgreSQL at the next start.\n", encoding="utf-8")
    db_move.failed(sqlite_file).unlink(missing_ok=True)
    asyncio.get_running_loop().call_later(RESTART_AFTER_SECONDS, os.kill, os.getpid(), signal.SIGTERM)
    return {"restarting": True}


@router.get("/status", response_model=HealthOut)
async def status(session: Session, runs: Runs, who: Who, project_id: list[int] | None = Query(default=None)) -> HealthOut:
    """Why nothing runs (services/health.py): pauses, runners, managers, subscriptions, for what the user sees."""
    return await health_service.check(session, runs.runners, who.user, project_id)


@router.get("/pause", response_model=PauseOut)
async def get_pause(session: Session) -> PauseOut:
    """Whether everything is paused, and what still moves."""
    return await pause_service.status(session)


@router.post("/pause", response_model=PauseOut)
async def pause_everything(session: Session, runs: Runs, who: Who) -> PauseOut:
    """Bring all projects to rest, e.g. before an update. Again: also what started meanwhile."""
    await runs.pause_all(session, who.name)
    return await pause_service.status(session)


@router.delete("/pause", response_model=PauseOut)
async def resume_everything(session: Session, runs: Runs) -> PauseOut:
    await runs.resume_all(session)
    return await pause_service.status(session)


@router.get("/access-keys", response_model=list[AccessKeyOut])
async def list_access_keys(session: Session) -> list[AccessKeyOut]:
    return [AccessKeyOut.model_validate(k) for k in await access_key_service.list_all(session)]


@router.post("/access-keys", response_model=AccessKeyOut, status_code=201)
async def create_access_key(body: PublicApiKeyIn, session: Session) -> AccessKeyOut:
    """A key that may read the whole API and change nothing. The answer carries the key itself, this once."""
    key, secret = await access_key_service.create(session, body.label)
    out = AccessKeyOut.model_validate(key)
    out.key = secret
    return out


@router.delete("/access-keys/{key_id}", status_code=204)
async def revoke_access_key(key_id: int, session: Session) -> None:
    await access_key_service.delete(session, key_id)


@router.get("/inspect/schema")
async def inspect_schema() -> list[dict[str, Any]]:
    """The database's tables, their sizes and readable columns."""
    return await db_read_service.schema()


@router.get("/inspect/query")
async def inspect_query(sql: str, limit: int = Query(default=200, ge=1), cell_chars: int = Query(default=2000, ge=0)) -> dict[str, Any]:
    """One SELECT on the database, read-only. Credentials read as NULL."""
    return await db_read_service.query(sql, limit, cell_chars)


@router.get("/limits", response_model=LimitsOut)
async def limits(session: Session, runs: Runs, who: Who) -> LimitsOut:
    runs.refresh_limits()
    out = await limits_service.current(session)
    if who.user is not None and not who.user.is_admin:  # what the user and their projects may use
        seen = {a.id for a in await account_service.visible(session, who.user)}
        out.accounts = [a for a in out.accounts if a.account_id in seen]
    return out


@router.get("/usage", response_model=UsageOut)
async def usage(session: Session, runs: Runs) -> UsageOut:
    """Disk and memory, in total, per project and agent; folder sizes as last measured."""
    return await usage_service.overview(session, runs)


@router.get("/usage/tokens", response_model=TokenUsageOut)
async def token_usage(session: Session, who: Who, days: int = Query(default=30, ge=1, le=90)) -> TokenUsageOut:
    """Tokens and cost per project and day, to compare the projects: an admin's all, everyone else's those they see."""
    ids = None if who.user is None or who.user.is_admin else set(await user_service.roles(session, who.user))
    return await stats_service.tokens(session, ids, days)


@router.post("/usage/measure", response_model=UsageOut)
async def measure_usage(session: Session, runs: Runs) -> UsageOut:
    """Measure the folders again, in the background."""
    return await usage_service.overview(session, runs, measure=True)


@router.get("/search/status", response_model=SearchStatusOut)
async def search_status(session: Session) -> SearchStatusOut:
    """Semantic search: whether it exists here and is on, its model, and how much is indexed."""
    return SearchStatusOut(**await search_service.status(session))


@router.post("/search/model", response_model=SearchStatusOut)
async def choose_search_model(body: SearchModelIn, session: Session) -> SearchStatusOut:
    """Another embedding model: the index is made anew for it in the background, search switches when it is
    complete. The current one again: a switch under way is called off."""
    await search_service.choose_model(session, body.model)
    await session.commit()
    return await search_status(session)


@router.get("/settings", response_model=SettingsOut)
async def get_settings(session: Session) -> SettingsOut:
    return SettingsOut(
        values=await settings_service.public_view(session),
        secrets=[k for k, d in settings_service.SETTINGS.items() if d.secret],
        env_fallbacks=settings_service.env_fallbacks(),
    )


@router.put("/settings", response_model=SettingsOut)
async def put_settings(body: SettingsUpdate, session: Session, runs: Runs, request: Request, background: BackgroundTasks) -> SettingsOut:
    try:
        await settings_service.update(session, body.values)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    runs.recheck()  # more parallel agents: waiting turns may start now
    if {"own_accounts", "own_accounts_off"} & body.values.keys():
        await account_service.switch_off_own(session)
    if "update_check" in body.values:
        background.add_task(request.app.state.updates.tick)  # switched on: don't wait hours for the first answer
    return await get_settings(session)


class SshKeyIn(BaseModel):
    private_key: str | None = None  # empty: PlainBoard creates a key pair


@router.get("/settings/attachment-retention", response_model=RetentionPreviewOut)
async def default_retention_preview(session: Session, days: int = Query(ge=0, le=3650), min_mb: int = Query(default=0, ge=0, le=100000)) -> RetentionPreviewOut:
    """What a default retention would delete in all projects that follow it, before it is saved."""
    return await retention_service.preview_default(session, days, min_mb, utcnow())


@router.post("/settings/git-ssh-key", response_model=SettingsOut)
async def set_git_ssh_key(body: SshKeyIn, session: Session) -> SettingsOut:
    """Create (or take over) the SSH key for all repositories with an SSH address. Replaces the old one."""
    await settings_service.set_ssh_key(session, (body.private_key or "").strip() or None)
    return await get_settings(session)


@router.delete("/settings/git-ssh-key", response_model=SettingsOut)
async def delete_git_ssh_key(session: Session) -> SettingsOut:
    await settings_service.set_ssh_key(session, "")
    return await get_settings(session)


class GithubCheckIn(BaseModel):
    token: str | None = None


@router.post("/settings/check/github", response_model=CheckOut)
async def check_github(body: GithubCheckIn, session: Session, runner: Runner, who: Who) -> CheckOut:
    if not body.token and not who.is_admin:
        return CheckOut(ok=False, detail="Enter a token to test.")
    values = await settings_service.get_all(session)
    result = await runner.check_github(settings_service.credentials(values, body.token))
    return CheckOut(ok=result.ok, detail=result.detail)
