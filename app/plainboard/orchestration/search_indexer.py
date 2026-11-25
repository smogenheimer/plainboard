"""Keeps the semantic search index current, without a model turn and without holding anything up.

Every ``search_interval_seconds`` it reads, per project with search on, what each indexer's source
changed since its cursor (the first time: everything, in batches, which is the backfill), brings
those chunks up to date (unchanged text keeps its vector) and then embeds what waits, newest first,
for at most ``EMBED_SECONDS`` a look, in small transactions. The model runs in its own thread at lowered
priority (``services/search/embedding.py``): requests and agent turns go on meanwhile. While there is
more it goes on after a short pause, else it waits for the next look. Text files attached to tickets are read a few at a time in a thread (``indexers.Files``). Commits come from the runner
(``/git/log``), per repository from its last indexed commit, at most every ``COMMITS_EVERY``, and its older
history page by page down to the root (``OLDER_EVERY``). Every
``SWEEP_EVERY`` what was deleted at its source leaves the index (deleting it already did, through the
foreign keys; this is the net under it), and so do commits no longer on their branch (a force push).
Each look that did something is a flow event ``search.indexed`` per project; failures are also shown in
the settings (``search.PROGRESS``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from plainboard_protocol import GitLogResult, GitLogSpec, GitPresentSpec
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Project, Repository, SearchCursor, SearchDocument, utcnow
from ..services import flow_log, permissions, search
from ..services import landings as landing_service
from ..services.search import indexers, store
from .run_manager import RunManager
from .runner_client import RunnerError

log = logging.getLogger(__name__)

BATCH = 200  # items per indexer and transaction
INDEX_SECONDS = 5.0  # reading the sources a look: quick, so a backfill has all its chunks early and embeds the newest first
EMBED_CHUNKS = 128  # chunks per call of the model and transaction (it batches them by length)
EMBED_SECONDS = 20.0  # embedding a look; then the cursors, commits and the sweep get their turn
BUSY_PAUSE = 1.0  # seconds between looks while there is more to do
COMMITS_EVERY = timedelta(minutes=10)
SWEEP_EVERY = timedelta(minutes=30)
COMMITS_FIRST = 1000  # commits of a repository a call: the newest the first time, then a page of the older history
OLDER_EVERY = timedelta(seconds=20)  # a page of a repository's older history at most this often
PRESENT_BATCH = 2000  # commits asked at once whether they are still on their branch


class SearchIndexer:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], runs: RunManager, interval: float):
        self._sm = sessionmaker
        self._runs = runs
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="search-indexer")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        await asyncio.sleep(self._interval)  # not in the middle of the start
        while True:
            try:
                more = await self.tick()
            except Exception as exc:
                log.exception("Search indexing failed")
                search.PROGRESS.failed(str(exc))
                more = False
            await asyncio.sleep(BUSY_PAUSE if more else self._interval)

    async def tick(self, now: datetime | None = None) -> bool:
        """One look; True while there is more to index or embed."""
        now = now or utcnow()
        if self._runs.paused:
            return False
        async with self._sm() as s:
            projects = await search.active_projects(s)
            if not projects:
                return False
            more, indexed = False, {}
            for project in projects:
                try:
                    indexed[project.id], busy = await self._index(s, project)
                    more |= busy
                    await self._sweep(s, project, now)
                    await self._commits(s, project, now)
                except Exception as exc:  # one project's trouble keeps neither the others nor the embedding from going on
                    await s.rollback()
                    log.exception("Indexing project %s for search failed", project.id)
                    search.PROGRESS.failed(f"{project.name}: {exc}")
            started = time.monotonic()
            busy, embedded, model = await self._embed(s, [p.id for p in projects])
            seconds = time.monotonic() - started
            for project in projects:
                chunks, vectors = indexed.get(project.id, 0), embedded.get(project.id, 0)
                if chunks or vectors:
                    await flow_log.record(
                        s, project.id, "search.indexed", chunks=chunks, embedded=vectors, model=model, seconds=round(seconds, 1),
                        rate=round(sum(embedded.values()) / seconds, 1) if embedded and seconds else None, more=busy or more,
                    )  # fmt: skip
            return busy or more

    async def _cursor(self, s: AsyncSession, project_id: int, source: str) -> SearchCursor:
        cursor = await s.get(SearchCursor, (project_id, source))
        if cursor is None:
            cursor = SearchCursor(project_id=project_id, source=source, value="", checked_at=datetime.min)
            s.add(cursor)
        return cursor

    async def _index(self, s: AsyncSession, project: Project) -> tuple[int, bool]:
        """Chunks changed, and whether a source has more than this look read."""
        changed, started = 0, time.monotonic()
        pending = list(indexers.INDEXERS)
        while pending and time.monotonic() - started < INDEX_SECONDS:
            for indexer in list(pending):
                cursor = await self._cursor(s, project.id, indexer.kind)
                batch = indexer.batch or BATCH
                items = await indexer.changed(s, project.id, cursor.value, batch)
                if items:
                    changed += await store.put(s, project.id, project.name, indexer.kind, [(source_id, doc) for _, source_id, doc in items])
                    cursor.value = items[-1][0]
                await s.commit()
                if len(items) < batch:
                    pending.remove(indexer)
        return changed, bool(pending)

    async def _sweep(self, s: AsyncSession, project: Project, now: datetime) -> None:
        cursor = await self._cursor(s, project.id, "sweep")
        if cursor.checked_at > now - SWEEP_EVERY:
            return
        for indexer in indexers.INDEXERS:
            await store.remove_missing(s, project.id, indexer.kind, indexer.existing(project.id))
        await s.execute(delete(SearchDocument).where(indexers.stale_commits(project.id)))
        cursor.checked_at = now
        await s.commit()
        await self._unreachable(s, project)

    async def _unreachable(self, s: AsyncSession, project: Project) -> None:
        """Commits no longer on the branch they were read from (a force push, the branch made anew) leave the
        index: asked in the runner's clone as last fetched. Not answered (a runner without it, no clone yet): they stay."""
        if not self._runs.runners.ready(project.id):
            return
        for repo in list(await s.scalars(select(Repository).where(Repository.project_id == project.id))):
            cursor = await s.get(SearchCursor, (project.id, f"commit:{repo.id}"))
            branch = cursor.value.split(" ", 1)[1] if cursor and " " in cursor.value else ""
            shas = await store.commit_shas(s, project.id, repo.id)
            await s.commit()
            if not branch or not shas:
                continue
            for start in range(0, len(shas), PRESENT_BATCH):
                part = shas[start : start + PRESENT_BATCH]
                try:
                    present = await self._runs.runners.of(project.id).git_present(GitPresentSpec(key=landing_service.train_key(repo), base=branch, shas=part))
                except (RunnerError, AttributeError) as exc:
                    log.info("Whether the commits of repository %s are still there could not be read: %s", repo.id, exc)
                    break
                gone = sorted(set(part) - set(present.on_base))
                if gone:
                    await store.remove_commits(s, project.id, repo.id, gone)
                    await s.commit()
                    log.info("%d commits of repository %s are no longer on %s: out of the search index", len(gone), repo.id, branch)

    async def _commits(self, s: AsyncSession, project: Project, now: datetime) -> None:
        """Per repository the commits since the last look, at most every ``COMMITS_EVERY``, and a page of its
        older history while that is not read to its root (cursor ``commit-old:<id>``: "<commit read from or
        done> <commits read of it> <the branch's commits>"), at most every ``OLDER_EVERY``."""
        if not self._runs.runners.ready(project.id):
            return  # an external runner that is away
        for repo in list(await s.scalars(select(Repository).where(Repository.project_id == project.id).order_by(Repository.id))):
            await self._newest(s, project, repo, now)
            await self._older(s, project, repo, now)

    async def _log(self, s: AsyncSession, project: Project, repo: Repository, cursor: SearchCursor, now: datetime, **spec) -> GitLogResult | None:
        log_spec = GitLogSpec(
            credentials=await permissions.credentials(s, project), key=landing_service.train_key(repo), remote=landing_service.remote(repo),
            base=repo.default_branch or None, limit=COMMITS_FIRST, **spec,
        )  # fmt: skip
        cursor.checked_at = now  # also after a failure: tried again later, not with every look
        await s.commit()  # nothing waits on the database while the runner works
        try:
            result = await self._runs.runners.of(project.id).git_log(log_spec)
        except (RunnerError, AttributeError) as exc:  # AttributeError: a runner client without it
            log.info("Reading the commits of repository %s failed: %s", repo.id, exc)
            return None
        docs = indexers.commit_docs(repo, result.branch, result.commits)
        await store.put(s, project.id, project.name, indexers.COMMIT, [(d.source_id, d) for d in docs])
        return result

    async def _newest(self, s: AsyncSession, project: Project, repo: Repository, now: datetime) -> None:
        cursor = await self._cursor(s, project.id, f"commit:{repo.id}")
        if cursor.checked_at > now - COMMITS_EVERY:
            return
        since = cursor.value.split(" ")[0] or None
        result = await self._log(s, project, repo, cursor, now, since=since)
        if result is None:
            return
        cursor = await self._cursor(s, project.id, f"commit:{repo.id}")
        cursor.value = f"{result.head} {result.branch}"  # where to go on from, and the branch it is on
        older = await self._cursor(s, project.id, f"commit-old:{repo.id}")
        if result.total is not None and not older.value:  # the first time (or the first since the older history is read): from here down
            read = len(result.commits) if since is None else 0
            older.value = f"{'done' if since is None and read < COMMITS_FIRST else result.head} {read} {result.total}"
        elif result.total is not None:
            older.value = " ".join(older.value.split(" ")[:2] + [str(result.total)])
        await s.commit()

    async def _older(self, s: AsyncSession, project: Project, repo: Repository, now: datetime) -> None:
        cursor = await self._cursor(s, project.id, f"commit-old:{repo.id}")
        if not cursor.value or cursor.value.startswith("done") or cursor.checked_at > now - OLDER_EVERY:
            return
        upto, read, _ = cursor.value.split(" ")
        result = await self._log(s, project, repo, cursor, now, upto=upto, skip=int(read))
        if result is None or result.total is None:
            return
        read = (int(read) if result.head == upto else 0) + len(result.commits)  # another commit: the branch was made anew, from its head again
        cursor = await self._cursor(s, project.id, f"commit-old:{repo.id}")
        cursor.value = f"{'done' if len(result.commits) < COMMITS_FIRST else result.head} {read} {result.total}"
        await s.commit()
        if len(result.commits) < COMMITS_FIRST:
            log.info("The history of repository %s is in the search index to its root: %d commits", repo.id, read)

    async def _embed(self, s: AsyncSession, project_ids: list[int]) -> tuple[bool, dict[int, int], str]:
        """The current model first (search stays current), then the one the index is being made anew for;
        once that one has every chunk, search switches to it. Returns whether more waits, the chunks
        embedded per project and the model."""
        more, done, name = False, {}, ""
        started = time.monotonic()
        for model in [await search.embedder(s), await search.next_embedder(s)]:
            if model is None or (more and time.monotonic() - started >= EMBED_SECONDS):
                continue
            before = sum(done.values())
            try:
                busy = await self._embed_with(s, model, project_ids, done, started)
            except Exception as exc:
                await s.rollback()
                log.exception("Embedding with %s failed", model.name)
                search.PROGRESS.failed(f"{model.name}: {exc}")
                busy = False
            more |= busy
            name = name or (model.name if sum(done.values()) > before else "")
        if not more and await search.switch_when_complete(s, project_ids):
            await s.commit()
        return more, done, name

    async def _embed_with(self, s: AsyncSession, model, project_ids: list[int], done: dict[int, int], started: float) -> bool:
        while time.monotonic() - started < EMBED_SECONDS:
            rows = await store.pending(s, model.name, project_ids, EMBED_CHUNKS)
            await s.commit()
            if not rows:
                return False
            if model.state != "ready":
                model.prepare()  # downloads and loads in the background; meanwhile search finds by words
                return False
            vectors = await model.passages([text for _, _, text, _ in rows])
            await store.store_vectors(s, model.name, rows, vectors)
            await s.commit()
            for _, project_id, _, _ in rows:
                done[project_id] = done.get(project_id, 0) + 1
            search.PROGRESS.embedded(len(rows))
            if len(rows) < EMBED_CHUNKS:
                return False
        return True
