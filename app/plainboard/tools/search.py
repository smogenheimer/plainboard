"""Searching the project's history by meaning and by words (services/search)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ..services import search
from .registry import SEARCH, ToolContext, ToolError, tool

Kind = Literal["ticket", "report", "decision", "comment", "note", "commit", "idea", "doc", "file"]


class SearchHistoryArgs(BaseModel):
    query: str = Field(min_length=2, max_length=500, description="What you look for, in any language")
    kinds: list[Kind] | None = Field(default=None, description="Only these")
    since: date | None = Field(default=None, description="Changed on or after")
    until: date | None = Field(default=None, description="Made on or before")
    repo: str | None = Field(default=None, description="Commits of this repository only")
    limit: int = Field(default=8, ge=1, le=20)


@tool(
    "search_history",
    "Search this project's history by meaning and keywords: tickets, assignment reports, decisions, comments, notes, git commits, open ideas, doc pages, text files attached to tickets. "
    "Use it before work that may have history (a similar bug, an earlier decision, where something was changed), not on every turn. "
    "Results are history and may be outdated: each says its age and where it stands now; check the current code or ticket before relying on it.",
    SearchHistoryArgs,
    feature=SEARCH,
)
async def search_history(ctx: ToolContext, args: SearchHistoryArgs) -> str:
    if not await search.enabled(ctx.session, ctx.project):
        raise ToolError("Semantic search is off for this project.")
    hits = await search.find(
        ctx.session, ctx.project, args.query, kinds=list(args.kinds) if args.kinds else None,
        since=datetime.combine(args.since, datetime.min.time()) if args.since else None,
        until=datetime.combine(args.until, datetime.min.time()) + timedelta(days=1) if args.until else None,
        repo=args.repo, limit=args.limit, who={"agent_id": ctx.agent.id, "assignment_id": ctx.run.assignment_id, "task_id": ctx.run.task_id},
    )  # fmt: skip
    now = await search.states(ctx.session, ctx.project, hits, ctx.runs.runners.of(ctx.project.id))
    return search.format_hits(hits, args.query, now)
