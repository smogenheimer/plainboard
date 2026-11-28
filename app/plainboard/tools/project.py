from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..models import utcnow
from ..schemas import HealthOut, ProjectUpdate
from ..services import health as health_service
from ..services import preview as preview_service
from ..services import projects as project_service
from ..services import repositories as repo_service
from ..services import settings as settings_service
from .registry import MANAGER, ToolContext, tool


class UpdateProjectArgs(BaseModel):
    name: str | None = None
    description: str | None = None
    language: str | None = Field(default=None, description="What people are written to in, e.g. German")
    workspace_path: str | None = None
    setup_script: str | None = Field(default=None, description="Full replacement. Must be safe to run repeatedly")
    auto_wake_manager: bool | None = None
    continue_with_backlog: bool | None = Field(default=None, description="When nothing in To do can start, go on with the Backlog")


@tool("get_project", "Project settings (name, description, workspace, repositories, setup script).", roles=(MANAGER,))
async def get_project(ctx: ToolContext, _args) -> str:
    p = ctx.project
    workspace = f"shared folder '{p.workspace_path}'" if p.workspace_path else "one directory per agent"
    repos = ", ".join(r.name for r in await repo_service.list_for_project(ctx.session, p.id)) or "-"
    limit = int(await settings_service.get(ctx.session, "manager_parallel_limit"))
    parallel = f"{await project_service.parallel_limit(ctx.session, p)} ({f'you may set 1-{limit}' if limit >= 1 else 'only the user can change it'}; this project only)"
    shares = await preview_service.shares_for_project(ctx.session, p.id)
    shared = "; ".join(f"{s.port} {s.access} until {s.expires_at:%Y-%m-%d}" for s in shares) or "none (all private)"
    return (
        f"Name: {p.name}\nLanguage: {p.language or 'each person their own'}\nRepositories: {repos}\nWorkspace: {workspace}\nParallel agents: {parallel}\nShared previews: {shared}\n"
        f"Auto-wake manager: {p.auto_wake_manager}\nBacklog when To do cannot start: {'worked on' if p.continue_with_backlog else 'left until the user moves it'}\nDescription:\n{p.description or '-'}\nSetup script:\n{p.setup_script or '-'}"
    )


@tool("get_system_status", "Why something does not run: pauses (everything, this project, limits), runner, manager, auto-wake, members paused.", roles=(MANAGER,))
async def get_system_status(ctx: ToolContext, _args) -> str:
    issues = [found] if (found := await health_service.everything(ctx.session)) else []
    issues += await health_service.of_project(ctx.session, ctx.runs.runners, ctx.project)
    return health_service.lines(HealthOut(ok=not any(i.blocking for i in issues), issues=issues, checked_at=utcnow()))


@tool("update_project", "Update project name, description, language, shared workspace folder, setup script, auto-wake or whether the Backlog is worked on.", UpdateProjectArgs, roles=(MANAGER,))
async def update_project(ctx: ToolContext, args: UpdateProjectArgs) -> str:
    await project_service.update(ctx.session, ctx.project, ProjectUpdate(**args.model_dump(exclude_unset=True)))
    return "Project updated."


class ParallelArgs(BaseModel):
    count: int


@tool(
    "set_parallel_agents",
    "How many agents of this project work at the same time. More finishes sooner but uses the token quota faster. Range: see get_project.",
    ParallelArgs,
    roles=(MANAGER,),
)
async def set_parallel_agents(ctx: ToolContext, args: ParallelArgs) -> str:
    await project_service.set_parallel_by_manager(ctx.session, ctx.project, args.count)
    ctx.runs.recheck()
    return f"Up to {args.count} agents now work at the same time."


class SharePreviewArgs(BaseModel):
    port: int
    access: Literal["private", "password", "public"] = Field(description="private: only the signed-in user (default for every preview). password: anyone with link and password. public: anyone with the link")
    password: str | None = Field(default=None, min_length=6, max_length=100, description="For access=password; omit to get one generated")
    days: int = Field(default=7, ge=1, le=30, description="After that it is private again")


@tool("share_preview", "Let people without a PlainBoard login open /preview/<port>/, or make it private again. Current shares: get_project.", SharePreviewArgs, roles=(MANAGER,))
async def share_preview(ctx: ToolContext, args: SharePreviewArgs) -> str:
    row, generated = await preview_service.share(
        ctx.session, ctx.project.id, args.port, args.access, password=args.password, days=args.days, agent_id=ctx.agent.id
    )
    link = f"/preview/{args.port}/"
    if row is None:
        return f"{link} is private again."
    if row.access == "public":
        return f"{link} is open to anyone with the link for {args.days} days."
    secret = f"the password {generated}" if generated else "the password you set"
    return f"{link} opens with {secret} for {args.days} days. Give the user the link and the password."
