"""Semantic search over a project's history (tickets, reports, decisions, comments, notes, commits, text files of tickets) and its open ideas.

Only with PostgreSQL and pgvector (``schema``), and while the setting ``semantic_search`` and the
project's own switch are on. ``indexers`` read the sources, ``documents`` prepares their texts, the
watcher ``orchestration/search_indexer.py`` keeps the index current and embeds in the background,
``store`` finds by meaning and by words together. Agents search with one tool (``tools/search.py``);
nothing searches on every turn. Every hit says how old it is and where its item stands now: history may
be outdated. ARCHITECTURE.md, "Semantic search", has the whole picture.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from datetime import datetime

from plainboard_protocol import GitPresentSpec
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Attachment, Decision, Project, Repository, SearchCursor, Task, utcnow
from .. import flow_log
from .. import settings as settings_service
from ..errors import ServiceError
from . import embedding, store
from .indexers import COMMIT, FILE, INDEXERS, KINDS
from .schema import available
from .store import Hit

__all__ = ["KINDS", "PROGRESS", "Hit", "active_projects", "available", "choose_model", "embedder", "enabled", "find", "for_user", "format_hits", "related", "states", "status"]

log = logging.getLogger(__name__)

HINT_COUNT = 3
PRESENT_TIMEOUT = 5.0  # seconds for the runner to say whether commits and files are still there; else it is left out


class Progress:
    """What the indexer met lately, for the settings: its last failures, and when it last embedded."""

    def __init__(self) -> None:
        self.errors: deque[tuple[datetime, str]] = deque(maxlen=5)
        self.embedded_at: datetime | None = None

    def failed(self, message: str) -> None:
        self.errors.append((utcnow(), message[:300]))

    def embedded(self, count: int) -> None:
        self.embedded_at = utcnow()


PROGRESS = Progress()


async def _on(s: AsyncSession) -> bool:
    return await available(s) and bool(await settings_service.get(s, "semantic_search"))


async def enabled(s: AsyncSession, project: Project) -> bool:
    return bool(project.semantic_search) and await _on(s)


async def active_projects(s: AsyncSession) -> list[Project]:
    if not await _on(s):
        return []
    return list(await s.scalars(select(Project).where(Project.semantic_search.is_(True)).order_by(Project.id)))


# --- models: the current one, and the next one while the index is made anew for it -----------------------


async def embedder(s: AsyncSession) -> embedding.Embedder:
    """The model search uses."""
    return embedding.get(await settings_service.get(s, "semantic_search_model"))


async def next_embedder(s: AsyncSession) -> embedding.Embedder | None:
    """The model the index is being made anew for, if a switch is under way."""
    name = await settings_service.get(s, "semantic_search_next")
    return embedding.get(name) if name and name in embedding.CARDS else None


async def choose_model(s: AsyncSession, name: str) -> None:
    """Another model: the index is made anew for it in the background while search keeps the current one;
    when every chunk has its vector, search switches at once and the old vectors go. The current model
    again: a switch under way is called off."""
    if name not in embedding.CARDS:
        raise ServiceError(f"Unknown model: {name}")
    current = await settings_service.get(s, "semantic_search_model")
    under_way = await settings_service.get(s, "semantic_search_next")
    if name == under_way:
        return
    if under_way and await available(s):
        await store.drop_model(s, embedding.get(under_way).name)
    if under_way:
        embedding.drop(under_way)
    await settings_service.update(s, {"semantic_search_next": None if name == current else name}, managed=True)


async def switch_when_complete(s: AsyncSession, project_ids: list[int]) -> bool:
    """The next model has a vector for every chunk: search uses it from now on, the old vectors go."""
    nxt = await next_embedder(s)
    if nxt is None or await store.waiting(s, nxt.name, project_ids):
        return False
    old = await settings_service.get(s, "semantic_search_model")
    await store.keep_only(s, nxt.name)
    await settings_service.update(s, {"semantic_search_model": await settings_service.get(s, "semantic_search_next"), "semantic_search_next": None}, managed=True)
    embedding.drop(old)
    log.info("Semantic search switched to the model %s", nxt.name)
    return True


# --- finding --------------------------------------------------------------------------------------------


async def _vector(s: AsyncSession, text: str) -> tuple[list[float] | None, str]:
    model = await embedder(s)
    if model.state != "ready":
        model.prepare()
        return None, model.name
    return await model.query(text), model.name


async def find(
    s: AsyncSession,
    project: Project,
    query: str,
    *,
    kinds: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    repo: str | None = None,
    limit: int = 8,
    who: dict | None = None,
) -> list[Hit]:
    """The hits, and a flow event ``search.query`` with what was asked, what came back with its scores and how
    long it took, so the quality can be judged on real searches (``who``: agent_id, assignment_id, task_id)."""
    started = time.monotonic()
    vector, model = await _vector(s, query)
    hits = await store.search(s, project.id, query, vector, model, kinds=kinds, since=since, until=until, repo=repo, limit=limit)
    filters = {k: v for k, v in {"kinds": kinds, "since": since and since.isoformat(), "until": until and until.isoformat(), "repo": repo, "limit": limit}.items() if v}
    await flow_log.record(
        s, project.id, "search.query", **(who or {}), query=query, filters=filters, model=model, by_meaning=vector is not None,
        ms=round((time.monotonic() - started) * 1000), hits=[_scores(h) for h in hits],
    )  # fmt: skip
    return hits


def _scores(hit: Hit) -> dict:
    return {"ref": hit.ref, "kind": hit.kind, "fused": round(hit.score, 5), "vector": None if hit.similarity is None else round(hit.similarity, 4), "vector_rank": hit.vector_rank, "word_rank": hit.word_rank}


async def related(s: AsyncSession, project: Project, text: str, ticket_number: int | None, who: dict | None = None) -> list[Hit]:
    """What is very close in meaning to a new piece of work, apart from its own ticket: above the model's
    own threshold (``ModelCard.hint_similarity``, calibrated on real tickets). Nothing while the model is not ready.
    The nearest ones and their similarity go into a flow event ``search.hint``, written with the turn."""
    if not text.strip() or not await enabled(s, project):
        return []
    model = await embedder(s)
    if model.state != "ready":
        return []
    started = time.monotonic()
    hits = await store.nearest(s, project.id, await model.query(text), model.name, exclude_ticket=ticket_number, limit=HINT_COUNT)
    shown = [h for h in hits if (h.similarity or 0) >= model.card.hint_similarity]
    await flow_log.record(
        s, project.id, "search.hint", commit=False, **(who or {}), model=model.name, threshold=model.card.hint_similarity,
        ms=round((time.monotonic() - started) * 1000), nearest=[_scores(h) for h in hits], shown=[h.ref for h in shown],
    )  # fmt: skip
    return shown


def _commit_url(repo_url: str, sha: str) -> str | None:
    """The commit's page on GitHub or GitLab; other hosts get no link."""
    url = repo_url.strip().removesuffix("/").removesuffix(".git")
    if url.startswith("git@"):  # git@github.com:owner/name
        url = "https://" + url[4:].replace(":", "/", 1)
    if url.startswith("https://github.com/"):
        return f"{url}/commit/{sha}"
    if url.startswith("https://gitlab.com/"):
        return f"{url}/-/commit/{sha}"
    return None


CLOSEST_WINDOW = 10  # where a filter looks for the drop in similarity


def closest(hits: list[Hit]) -> list[Hit]:
    """For a filter (the board's, #133): what matches by words, and by meaning only what stands out, the nearest
    down to the largest drop in similarity among the first few. Search by meaning always has a nearest, even for
    nonsense, and a model's scale is its own: no fixed threshold."""
    sims = sorted((h.similarity for h in hits if h.similarity is not None), reverse=True)[:CLOSEST_WINDOW]
    if len(sims) < 2:
        return hits
    drop = max(range(len(sims) - 1), key=lambda i: sims[i] - sims[i + 1])
    return [h for h in hits if h.word_rank is not None or (h.similarity is not None and h.similarity >= sims[drop])]


async def for_user(s: AsyncSession, project: Project, query: str, limit: int = 8, kinds: list[str] | None = None, only_close: bool = False) -> dict:
    """A person's search in the web UI (the command palette; the board's filter with ``kinds`` and ``only_close``): the state of search and the hits, each with what
    opens it (its ticket's id, a note's id, a commit's page). Off or not there: no hits and why, never an error."""
    if not await available(s):
        return {"state": "unavailable", "hits": []}
    if not await enabled(s, project):
        return {"state": "off", "hits": []}
    ready = (await embedder(s)).state == "ready"
    hits = await find(s, project, query, kinds=kinds or None, limit=limit)
    if only_close:
        hits = closest(hits)
    numbers = {h.ticket_number for h in hits if h.ticket_number}
    ids = dict((await s.execute(select(Task.number, Task.id).where(Task.project_id == project.id, Task.number.in_(numbers)))).all()) if numbers else {}
    repos = {r.id: r for r in await s.scalars(select(Repository).where(Repository.project_id == project.id))} if any(h.kind == COMMIT for h in hits) else {}
    out = []
    for h in hits:
        item = {
            "kind": h.kind, "ref": h.ref, "title": h.title, "snippet": snippet(h.body, query, 160), "at": h.at, "changed_at": h.changed_at,
            "ticket_number": h.ticket_number, "task_id": ids.get(h.ticket_number) if h.ticket_number else None, "repo": h.repo,
        }  # fmt: skip
        if h.kind == "note" and h.source_id.isdigit():
            item["note_id"] = int(h.source_id)
        if h.kind == "idea" and h.source_id.isdigit():
            item["idea_id"] = int(h.source_id)
        if h.kind == "doc" and h.source_id.isdigit():
            item["doc_page_id"] = int(h.source_id)
        if h.kind == FILE and h.source_id.isdigit():
            item |= {"attachment_id": int(h.source_id), "filename": h.ref}
            if h.ticket_number is None:
                idea_id = await s.scalar(select(Attachment.idea_id).where(Attachment.id == int(h.source_id), Attachment.project_id == project.id))
                item["idea_id"] = idea_id
        if h.kind == COMMIT:
            repo_id, _, sha = h.source_id.partition(":")
            repo = repos.get(int(repo_id)) if repo_id.isdigit() else None
            item |= {"sha": sha, "url": _commit_url(repo.url, sha) if repo else None}
        out.append(item)
    return {"state": "ok" if ready else "preparing", "hits": out}


# --- where a hit's item stands now ---------------------------------------------------------------------


async def states(s: AsyncSession, project: Project, hits: list[Hit], runner=None) -> list[str]:
    """For each hit, what is true of its item now, where that is cheap to know: the ticket's status, a newer
    decision, whether a commit is still on its branch and its files still exist ("" if nothing to say)."""
    numbers = {h.ticket_number for h in hits if h.ticket_number}
    tickets = {t.number: t for t in await s.scalars(select(Task).where(Task.project_id == project.id, Task.number.in_(numbers)))} if numbers else {}
    newest = {}
    if any(h.kind == "decision" for h in hits) and tickets:
        rows = await s.execute(select(Decision.task_id, func.max(Decision.id)).where(Decision.task_id.in_([t.id for t in tickets.values()])).group_by(Decision.task_id))
        newest = dict(rows.all())
    commits = await _commits_now(s, project, [h for h in hits if h.kind == "commit"], runner)
    out = []
    for h in hits:
        if h.kind == "commit":
            out.append(commits.get(h.source_id, ""))
            continue
        task = tickets.get(h.ticket_number) if h.ticket_number else None
        if h.ticket_number and task is None:
            out.append(f"ticket #{h.ticket_number} was deleted")
            continue
        parts = []
        if h.kind == "decision":
            decision = await s.get(Decision, int(h.source_id))
            if decision is not None:
                parts.append(f"decision {decision.status}")
                if task is not None and newest.get(task.id, 0) > decision.id:
                    parts.append(f"a newer decision on #{task.number} exists")
        if task is not None:
            parts.append(f"ticket now {task.status}" + (", archived" if task.archived_at else ""))
        out.append("; ".join(parts))
    return out


async def _commits_now(s: AsyncSession, project: Project, hits: list[Hit], runner) -> dict[str, str]:
    """Through the runner's clone as last fetched, per repository: one call, a short wait at most."""
    if not hits or runner is None:
        return {}
    from ..landings import train_key

    out: dict[str, str] = {}
    by_repo: dict[int, list[Hit]] = {}
    for h in hits:
        repo_id, _, _ = h.source_id.partition(":")  # a commit's source id: <repository id>:<sha>
        if repo_id.isdigit():
            by_repo.setdefault(int(repo_id), []).append(h)
    for repo_id, group in by_repo.items():
        repo = await s.get(Repository, repo_id)
        cursor = await s.get(SearchCursor, (project.id, f"commit:{repo_id}"))
        branch = (cursor.value.split(" ", 1)[1] if cursor and " " in cursor.value else "") or (repo.default_branch if repo else "")
        if repo is None or repo.project_id != project.id or not branch:
            continue
        paths = sorted({p for h in group for p in (h.paths or [])})[:300]
        spec = GitPresentSpec(key=train_key(repo), base=branch, shas=[h.source_id.partition(":")[2] for h in group][:50], paths=paths)
        try:
            present = await asyncio.wait_for(runner.git_present(spec), PRESENT_TIMEOUT)
        except Exception as exc:  # an older runner, one that is away, or slow: the hit goes without it
            log.info("Whether commits of %s are still there could not be read: %s", repo.name, exc)
            continue
        missing = set(present.missing)
        for h in group:
            sha = h.source_id.partition(":")[2]
            words = [f"still on {branch}" if sha in present.on_base else f"no longer on {branch}"]
            gone = [p for p in (h.paths or []) if p in missing]
            if gone:
                words.append(f"{len(gone)} of its files no longer exist" + (f" ({', '.join(gone[:3])}{', …' if len(gone) > 3 else ''})" if gone else ""))
            out[h.source_id] = "; ".join(words)
    return out


# --- what an agent reads ------------------------------------------------------------------------------


def ago(then: datetime, now: datetime) -> str:
    days = (now - then).total_seconds() / 86400
    if days < 1 and now.date() == then.date():
        return "today"
    if days < 2:
        return "yesterday"
    if days < 14:
        return f"{int(days)} days ago"
    if days < 60:
        return f"{int(days // 7)} weeks ago"
    if days < 730:
        return f"{int(days // 30)} months ago"
    return f"{int(days // 365)} years ago"


def snippet(body: str, query: str, size: int = 240) -> str:
    """The part of a chunk around the first word of the search it has, else its start."""
    flat = re.sub(r"\s+", " ", body).strip()
    if len(flat) <= size:
        return flat
    start = 0
    lowered = flat.lower()
    for word in store.keyword_query(query).replace(":*", "").split(" | "):
        at = lowered.find(word) if word else -1
        if at >= 0:
            start = max(0, at - size // 3)
            break
    if start:
        space = flat.find(" ", start)
        start = space + 1 if 0 <= space < start + 20 else start
    text = flat[start : start + size].rstrip()
    return ("…" if start else "") + text + ("…" if start + size < len(flat) else "")


def label(hit: Hit, now: datetime | None = None, state: str = "") -> str:
    """A hit in one line: its reference and kind, when it was made and last changed (with its age), where it stands now, its title."""
    now = now or utcnow()
    where = f" on #{hit.ticket_number}" if hit.ticket_number and hit.kind in ("report", FILE) else ""
    if hit.kind == FILE and hit.paths:  # the file to read
        where += f" ({hit.paths[0]})"
    repo = f" in {hit.repo}" if hit.repo else ""
    kind = "" if hit.kind in ("decision", "comment") else f" {hit.kind}"  # their reference names it
    when = f"{hit.at:%Y-%m-%d} ({ago(hit.at, now)})"
    if hit.changed_at and hit.changed_at.date() > hit.at.date():
        when += f", changed {hit.changed_at:%Y-%m-%d} ({ago(hit.changed_at, now)})"
    return f"{hit.ref}{kind}{where}{repo}, {when}" + (f"; now: {state}" if state else "") + f": {hit.title}"


def format_hits(hits: list[Hit], query: str, now_states: list[str] | None = None, now: datetime | None = None) -> str:
    if not hits:
        return "Nothing found."
    now_states = now_states or [""] * len(hits)
    return "\n".join(f"- {label(h, now, st)}\n  {snippet(h.body, query)}" for h, st in zip(hits, now_states, strict=True))


# --- the settings page ---------------------------------------------------------------------------------


def _model_view(model: embedding.Embedder | None) -> dict | None:
    if model is None:
        return None
    return {"name": model.name, "label": model.card.label, "state": model.state, "progress": model.progress, "error": model.error}


async def _totals(s: AsyncSession) -> dict[str, int]:
    """How many items each source has in the projects with search on (commits: as many as the runner counted on
    each repository's branch, without merges; a repository it has not counted yet: as many as were read)."""
    out: dict[str, int] = {}
    for project in await active_projects(s):
        for indexer in INDEXERS:
            query = indexer.existing(project.id).subquery()
            out[indexer.kind] = out.get(indexer.kind, 0) + int(await s.scalar(select(func.count()).select_from(query)) or 0)
        for repo_id in await s.scalars(select(Repository.id).where(Repository.project_id == project.id)):
            cursor = await s.get(SearchCursor, (project.id, f"commit-old:{repo_id}"))
            parts = cursor.value.split(" ") if cursor else []
            out[COMMIT] = out.get(COMMIT, 0) + (int(parts[2]) if len(parts) == 3 else len(await store.commit_shas(s, project.id, repo_id)))
    return out


async def status(s: AsyncSession) -> dict:
    """For the settings, polled while it works: the models (download share, state, errors), per source the items
    there, indexed and embedded, the throughput, how long the rest takes, and what failed lately."""
    model, nxt = await embedder(s), await next_embedder(s)
    out: dict = {"available": await available(s), "enabled": bool(await settings_service.get(s, "semantic_search")), "model": _model_view(model), "next": _model_view(nxt)}
    names = [model.name] + ([nxt.name] if nxt else [])
    counts = await store.counts(s, names) if out["available"] else {"documents": 0, "by_kind": {}, "vectors": dict.fromkeys(names, 0)}
    totals = await _totals(s) if out["available"] and out["enabled"] else {}
    by_kind = {kind: counts["by_kind"].get(kind, {"items": 0, "chunks": 0, "embedded": 0}) | {"total": totals.get(kind)} for kind in KINDS if kind in totals or kind in counts["by_kind"]}
    documents = counts["documents"]
    embedded, next_embedded = counts["vectors"][model.name], counts["vectors"][nxt.name] if nxt else 0
    rate = (nxt or model).rate if nxt else model.rate
    rest = (documents - next_embedded) if nxt else (documents - embedded)
    out |= {
        "documents": documents, "by_kind": by_kind, "embedded": embedded, "next_embedded": next_embedded,
        "rate": round(rate, 1) if rate else None, "eta_seconds": int(rest / rate) if rate and rest > 0 else None,
        "errors": [{"at": at, "message": message} for at, message in PROGRESS.errors],
    }  # fmt: skip
    # How long a full index takes with each model, here: its measured speed, scaled by how fast the current
    # model ran here against its own measured speed (until it has run: by this server's threads).
    scale = (model.rate / model.card.speed) if model.rate and model.card.speed else min(embedding.threads(), embedding.REFERENCE_THREADS) / embedding.REFERENCE_THREADS
    out["models"] = [
        {
            "name": c.name, "label": c.label, "tier": c.tier, "description": c.description, "dim": c.dim, "max_tokens": c.max_tokens,
            "licence": c.licence, "download_mb": c.download_mb, "ram_mb": c.ram_mb, "mmteb": c.mmteb, "mmteb_retrieval": c.mmteb_retrieval,
            "recommended": c.recommended, "estimate_seconds": int(documents / (c.speed * scale)) if c.speed else None,
        }
        for c in embedding.MODELS
    ]  # fmt: skip
    return out
