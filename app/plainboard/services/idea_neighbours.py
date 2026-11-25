"""Which ideas are close to which: the neighbourhood the model is shown when it sorts ideas, reads what a person
typed or rearranges the topics, so that a board of hundreds of ideas never goes to the model whole
(services/ideas.py).

By meaning where semantic search runs for the project: ideas are chunks of kind ``idea`` in the search index
(services/search/indexers.Ideas); one that has no vector yet is embedded on the spot. Always also by shared
words (TF-IDF over title and text, words cut to their first letters so that "Zahlung" meets "Zahlungen"), fused
by rank with the meaning: it finds the same wording and ideas the background indexer has not embedded yet.
Without semantic search (SQLite, search off, the model not ready) the words alone decide: cheaper, but blind to
synonyms and to the same idea in another language.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Idea, Project
from . import search
from .search import embedding, indexers, store

log = logging.getLogger(__name__)

MEANING, WORDS = "meaning", "words"
STEM = 6  # letters of a word that count: inflections meet
RRF_K = 60  # as in search: the top places of both lists count
_WORD = re.compile(r"[^\W_]{3,}")


def _terms(value: str) -> list[str]:
    return [w[:STEM] for w in _WORD.findall(value.lower()) if w not in store.STOPWORDS]


def idea_text(idea: Idea) -> str:
    return f"{idea.title}\n{idea.text}"


class Words:
    """TF-IDF vectors of a pool of texts, compared by cosine."""

    def __init__(self, texts: dict[int, str]):
        counts = {key: Counter(_terms(value)) for key, value in texts.items()}
        df = Counter(term for c in counts.values() for term in c)
        self.n = len(texts)
        self.idf = {term: math.log((1 + self.n) / (1 + d)) + 1 for term, d in df.items()}
        self.vectors = {key: self._weigh(c) for key, c in counts.items()}

    def _weigh(self, counts: Counter) -> dict[str, float]:
        unseen = math.log(1 + self.n) + 1
        v = {term: (1 + math.log(c)) * self.idf.get(term, unseen) for term, c in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {term: x / norm for term, x in v.items()}

    def vector(self, value: str) -> dict[str, float]:
        return self._weigh(Counter(_terms(value)))

    def nearest(self, v: dict[str, float], among: list[int], k: int, exclude: int | None = None) -> list[int]:
        scored = []
        for key in among:
            if key == exclude:
                continue
            other = self.vectors.get(key) or {}
            score = sum(x * other[t] for t, x in v.items() if t in other)
            if score > 0:
                scored.append((score, key))
        scored.sort(key=lambda p: (-p[0], p[1]))
        return [key for _, key in scored[:k]]


def fuse(lists: list[list[int]], k: int) -> list[int]:
    """Reciprocal rank fusion of ranked lists of ids."""
    score: dict[int, float] = defaultdict(float)
    for ranked in lists:
        for rank, key in enumerate(ranked):
            score[key] += 1.0 / (RRF_K + rank)
    return sorted(score, key=lambda key: (-score[key], key))[:k]


def spread(lists: list[list[int]], limit: int, skip: set[int] | None = None) -> list[int]:
    """The first of every list, then the second of every one ...: each idea gets its closest ones before any
    gets its farther ones. At most ``limit``."""
    out: list[int] = []
    seen = set(skip or ())
    for rank in range(max((len(x) for x in lists), default=0)):
        for ranked in lists:
            if rank < len(ranked) and ranked[rank] not in seen:
                seen.add(ranked[rank])
                out.append(ranked[rank])
                if len(out) >= limit:
                    return out
    return out


# --- by meaning ----------------------------------------------------------------------------------------


async def _model(s: AsyncSession, project: Project) -> embedding.Embedder | None:
    if not await search.enabled(s, project):
        return None
    model = await search.embedder(s)
    return model if model.state == "ready" else None  # the indexer makes it ready


async def _embed(s: AsyncSession, project: Project, model: embedding.Embedder, ideas: list[Idea]) -> None:
    """These ideas in the index, each with its vector now: the indexer would come to them only with its next look."""
    await store.put(s, project.id, project.name, indexers.IDEA, [(str(i.id), indexers.idea_doc(i)) for i in ideas])
    await s.flush()
    rows = await store.pending(s, model.name, [project.id], 10_000, kind=indexers.IDEA, source_ids=[str(i.id) for i in ideas])
    if rows:
        await s.commit()  # nothing is held while the model embeds
        await store.store_vectors(s, model.name, rows, await model.passages([r[2] for r in rows]))
    await s.commit()


_PAIRS = """SELECT idea, other FROM (
    SELECT q.source_id AS idea, d.source_id AS other,
           row_number() OVER (PARTITION BY q.source_id ORDER BY max(1 - (v.embedding <=> qv.embedding)) DESC, d.source_id) AS r
    FROM search_documents q JOIN search_vectors qv ON qv.document_id = q.id AND qv.model = :model
    JOIN search_documents d ON d.project_id = q.project_id AND d.kind = q.kind AND d.source_id <> q.source_id
    JOIN search_vectors v ON v.document_id = d.id AND v.model = :model
    WHERE q.project_id = :project AND q.kind = :kind AND q.source_id = ANY(:ids) AND d.source_id = ANY(:pool)
    GROUP BY q.source_id, d.source_id) x
    WHERE r <= :k ORDER BY idea, r"""

_TEXT = """SELECT d.source_id AS id FROM search_documents d JOIN search_vectors v ON v.document_id = d.id AND v.model = :model
    WHERE d.project_id = :project AND d.kind = :kind AND d.source_id = ANY(:pool)
    GROUP BY d.source_id ORDER BY max(1 - (v.embedding <=> CAST(:vector AS vector))) DESC, d.source_id LIMIT :k"""


# --- what callers use ---------------------------------------------------------------------------------


async def around(s: AsyncSession, project: Project, ideas: list[Idea], pool: list[Idea], k: int) -> tuple[dict[int, list[int]], str]:
    """For each of ``ideas`` the ``k`` closest of ``pool`` (never itself), closest first, and by what they were
    found: ``meaning`` (with words) or ``words`` only."""
    pool_ids = [i.id for i in pool]
    words = Words({i.id: idea_text(i) for i in [*pool, *ideas]})
    by_words = {i.id: words.nearest(words.vectors[i.id], pool_ids, k, exclude=i.id) for i in ideas}
    model = await _model(s, project) if ideas and pool else None
    if model is None:
        return by_words, WORDS
    try:
        await _embed(s, project, model, ideas)
        params = {"model": model.name, "project": project.id, "kind": indexers.IDEA, "ids": [str(i.id) for i in ideas], "pool": [str(i) for i in pool_ids], "k": k}
        by_meaning: dict[int, list[int]] = defaultdict(list)
        for row in await s.execute(text(_PAIRS), params):
            by_meaning[int(row.idea)].append(int(row.other))
    except Exception:
        log.exception("Ideas of project %s: finding neighbours by meaning failed, by words instead", project.id)
        await s.rollback()
        return by_words, WORDS
    return {i.id: fuse([by_meaning.get(i.id, []), by_words[i.id]], k) for i in ideas}, MEANING


async def near_text(s: AsyncSession, project: Project, value: str, pool: list[Idea], k: int) -> tuple[list[int], str]:
    """The ``k`` ideas of ``pool`` closest to a text (what a person typed), closest first, and by what."""
    pool_ids = [i.id for i in pool]
    words = Words({i.id: idea_text(i) for i in pool})
    by_words = words.nearest(words.vector(value), pool_ids, k)
    model = await _model(s, project) if pool and value.strip() else None
    if model is None:
        return by_words, WORDS
    try:
        vector = await model.query(value)
        params = {"model": model.name, "project": project.id, "kind": indexers.IDEA, "pool": [str(i) for i in pool_ids], "k": k, "vector": store.vector_literal(vector)}
        by_meaning = [int(r.id) for r in await s.execute(text(_TEXT), params)]
    except Exception:
        log.exception("Ideas of project %s: finding ideas by meaning failed, by words instead", project.id)
        await s.rollback()
        return by_words, WORDS
    return fuse([by_meaning, by_words], k), MEANING
