"""A project's ideas: a room for planning. Collecting and sorting them starts nothing; tickets come from them
only when the user asks the manager (tools/ideas.py) or takes one idea over by hand.

A model (``orchestration/ideas.py``) reads what a person types (a new idea, an addition to one, several,
merging, or a question back), sorts ideas into topics (islands on the board), merges ideas that say the same or
belong in one (``merge_request``/``apply_merge``), splits one that holds several independent ideas, reports
ideas that contradict each other to the person as a question (a conflict; never resolved on its own), and
rearranges the topics now and then. What a person placed or named by hand stays (``manual``).
Every step is an ``IdeaChange`` that can be undone, every replaced text an ``IdeaVersion``: no idea and no text
is ever lost. Only a person deletes an idea, on purpose.

Hundreds of ideas never go to the model whole: above ``BOARD_WHOLE`` open ideas it is shown the neighbourhood
of what it works on (services/idea_neighbours.py: by meaning through the search index, else by shared words),
and the topics are rearranged group by group of related topics (``reorder_groups``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, BinaryIO

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..events import bus
from ..models import Agent, Attachment, Comment, Idea, IdeaChange, IdeaLink, IdeaStatus, IdeaTopic, IdeaVersion, InboxItem, Landing, LandingStatus, Project, SearchDocument, Task, TaskStatus, utcnow
from ..schemas import IDEA_TEXT_MAX, AttachmentOut, IdeaChangeOut, IdeaCreate, IdeaOut, IdeaTicketIn, IdeaTopicIn, IdeaTopicOut, TaskCreate
from . import board as board_service
from . import thumbnails as thumbnail_service
from . import idea_neighbours as neighbours
from .search import schema as search_schema
from .errors import NotFound, ServiceError
from .util import format_bytes, truncate

COLORS = 10  # the design's --chart-1..10
PENDING, SORTED, FAILED = "pending", "sorted", "failed"
# Links: duplicate (to be merged, or left out as one by the manager); kept (were merged or asked about once:
# never merged on their own again, shown as similar); split (parts of one idea); conflict (they contradict
# each other: the person decides, shown on both); resolved (the person decided a conflict). Only kept, split,
# conflict and resolved keep two ideas from being merged on their own. similar: made by earlier versions
# (every close pair), no longer made nor shown: ideas that belong together are merged now, related ones share a topic.
SIMILAR, DUPLICATE, KEPT, SPLIT, CONFLICT, RESOLVED = "similar", "duplicate", "kept", "split", "conflict", "resolved"
APART = (KEPT, SPLIT, CONFLICT, RESOLVED)
SORT_TEXT = 600  # what the sorting model reads of an idea
NEAR_TEXT = 400  # ... of an idea near those it sorts
MERGE_TEXT = 8000  # what the model reads of an idea it merges with another; longer ones are merged as written
READ_TEXT = 12_000  # longer inputs are kept as written, not read: the model would have to write them out again
MERGE_GROUP = 6  # ideas merged into one at most at a time
SPLIT_PARTS = 6  # parts an idea is split into at most
SPLIT_KEEPS = 0.7  # the parts together keep at least this share of the idea's text, else it is not split
# Up to this many open ideas the model sees all of them; above, the neighbourhood of what it works on.
BOARD_WHOLE = 50
NEIGHBOURS = 6  # closest ideas per idea it sorts
NEARBY_MAX = 60  # ideas shown near those it sorts, at most
INTENT_NEAR = 25  # ideas closest to what a person typed
INTENT_RECENT = 10  # and the newest ones (an addition is often to what was just written)
REORDER_IDEAS = 80  # ideas per rearranging call: related topics go together up to this
OTHER_TOPICS = 100  # topics outside a group named in its call, at most
QUESTION_OPEN, QUESTION_ANSWERED, QUESTION_WITHDRAWN = "open", "answered", "withdrawn"
IDEA_FIELDS = (
    "title", "text", "status", "topic_id", "topic_manual", "task_id", "hint", "sort_state", "merged_into_id", "closed_at", "closed_reason",
    "question", "question_options", "question_status", "question_answer",
)  # fmt: skip
TOPIC_FIELDS = ("name", "color", "hint", "manual", "x", "y")


# --- reading -----------------------------------------------------------------------------------


async def get(session: AsyncSession, project_id: int, idea_id: int) -> Idea:
    idea = await session.get(Idea, idea_id)
    if idea is None or idea.project_id != project_id:
        raise NotFound(f"Idea {idea_id} not found")
    return idea


async def get_topic(session: AsyncSession, project_id: int, topic_id: int) -> IdeaTopic:
    topic = await session.get(IdeaTopic, topic_id)
    if topic is None or topic.project_id != project_id:
        raise NotFound("Topic not found")
    return topic


async def topics(session: AsyncSession, project_id: int) -> list[IdeaTopic]:
    return list((await session.scalars(select(IdeaTopic).where(IdeaTopic.project_id == project_id).order_by(IdeaTopic.id))).all())


async def open_ideas(session: AsyncSession, project_id: int) -> list[Idea]:
    query = select(Idea).where(Idea.project_id == project_id, Idea.status == IdeaStatus.OPEN).order_by(Idea.id)
    return list((await session.scalars(query)).all())


async def listed(session: AsyncSession, project_id: int, *, closed: bool = False) -> list[Idea]:
    """For the board: the open ones and those still being read; ``closed``: all."""
    query = select(Idea).where(Idea.project_id == project_id).order_by(Idea.id)
    if not closed:
        query = query.where(Idea.status.in_((IdeaStatus.OPEN, IdeaStatus.READING)))
    return list((await session.scalars(query)).all())


async def open_questions(session: AsyncSession, project_id: int) -> list[Idea]:
    """The open ideas the model asked about and nobody answered yet ("for you")."""
    query = select(Idea).where(Idea.project_id == project_id, Idea.status == IdeaStatus.OPEN, Idea.question_status == QUESTION_OPEN).order_by(Idea.id)
    return list((await session.scalars(query)).all())


async def versions(session: AsyncSession, idea: Idea) -> list[IdeaVersion]:
    return list((await session.scalars(select(IdeaVersion).where(IdeaVersion.idea_id == idea.id).order_by(IdeaVersion.id.desc()))).all())


async def get_change(session: AsyncSession, project_id: int, change_id: int) -> IdeaChange:
    change = await session.get(IdeaChange, change_id)
    if change is None or change.project_id != project_id:
        raise NotFound("Change not found")
    return change


async def changes(session: AsyncSession, project_id: int, limit: int = 20) -> list[IdeaChange]:
    query = select(IdeaChange).where(IdeaChange.project_id == project_id).order_by(IdeaChange.id.desc()).limit(limit)
    return list((await session.scalars(query)).all())


async def outs(session: AsyncSession, ideas: list[Idea]) -> list[IdeaOut]:
    """The ideas as the API shows them: with their ticket's number and the open ideas they are linked to: the one
    it is to be merged into, those kept apart from it on purpose (similar), those it contradicts."""
    if not ideas:
        return []
    ids = [i.id for i in ideas]
    links = (await session.scalars(select(IdeaLink).where(or_(IdeaLink.idea_id.in_(ids), IdeaLink.other_id.in_(ids))))).all()
    linked = {link.idea_id for link in links} | {link.other_id for link in links}
    still_open = set((await session.scalars(select(Idea.id).where(Idea.id.in_(linked), Idea.status == IdeaStatus.OPEN))).all()) if linked else set()
    task_ids = {i.task_id for i in ideas if i.task_id}
    numbers = dict((await session.execute(select(Task.id, Task.number).where(Task.id.in_(task_ids)))).all()) if task_ids else {}
    files: dict[int, list[AttachmentOut]] = {}
    for a in await session.scalars(select(Attachment).where(Attachment.idea_id.in_(ids)).order_by(Attachment.id)):
        files.setdefault(a.idea_id, []).append(AttachmentOut.model_validate(a))
    resolved = {frozenset((link.idea_id, link.other_id)) for link in links if link.kind == RESOLVED}
    result = []
    for idea in ideas:
        out = IdeaOut.model_validate(idea)
        out.task_number = numbers.get(idea.task_id) if idea.task_id else None
        out.attachments = files.get(idea.id, [])
        similar: set[int] = set()
        conflicts: set[int] = set()
        for link in links:
            if idea.id not in (link.idea_id, link.other_id):
                continue
            other = link.other_id if link.idea_id == idea.id else link.idea_id
            if other == idea.id or other not in still_open:
                continue
            if link.kind == DUPLICATE and link.idea_id == idea.id:
                out.duplicate_of = other
            elif link.kind == KEPT:
                similar.add(other)
            elif link.kind == CONFLICT and frozenset((idea.id, other)) not in resolved:
                conflicts.add(other)
        out.similar = sorted(similar - {out.duplicate_of} - conflicts)
        out.conflicts = sorted(conflicts)
        result.append(out)
    return result


async def change_out(session: AsyncSession, change: IdeaChange, *, with_ideas: bool = False) -> IdeaChangeOut:
    out = IdeaChangeOut.model_validate(change)
    if with_ideas:
        ids = set(change.summary.get("ideas") or [])
        found = list((await session.scalars(select(Idea).where(Idea.id.in_(ids)).order_by(Idea.id))).all()) if ids else []
        out.ideas = await outs(session, found)
    return out


async def publish(session: AsyncSession, project_id: int, ideas: list[Idea]) -> None:
    for out in await outs(session, ideas):
        bus.publish(project_id, "idea.upserted", out)


def publish_topic(topic: IdeaTopic) -> None:
    bus.publish(topic.project_id, "idea_topic.upserted", IdeaTopicOut.model_validate(topic))


async def _linked(session: AsyncSession, ids: list[int]) -> set[int]:
    query = select(IdeaLink.idea_id, IdeaLink.other_id).where(or_(IdeaLink.idea_id.in_(ids), IdeaLink.other_id.in_(ids)))
    return {a for pair in (await session.execute(query)).all() for a in pair} - set(ids)


async def _publish_linked(session: AsyncSession, project_id: int, ids: list[int], others: set[int] | None = None) -> None:
    """Ideas linked to these: what they show of their links changed."""
    others = await _linked(session, ids) if others is None else others
    if others:
        await publish(session, project_id, list((await session.scalars(select(Idea).where(Idea.id.in_(others), Idea.status == IdeaStatus.OPEN))).all()))


# --- changes that can be undone ----------------------------------------------------------------


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class Snap:
    """The state before a change, of what it touches: an idea or topic is taken before its first change."""

    def __init__(self) -> None:
        self.ideas: dict[int, dict[str, Any]] = {}
        self.topics: dict[int, dict[str, Any]] = {}
        self.new_ideas: list[int] = []
        self.new_topics: list[int] = []
        self.files: dict[int, int] = {}  # attachment -> the idea it belonged to
        self.new_links: list[int] = []

    def idea(self, idea: Idea) -> Idea:
        if idea.id not in self.ideas and idea.id not in self.new_ideas:
            self.ideas[idea.id] = {f: _plain(getattr(idea, f)) for f in IDEA_FIELDS}
        return idea

    def topic(self, topic: IdeaTopic) -> IdeaTopic:
        if topic.id not in self.topics and topic.id not in self.new_topics:
            self.topics[topic.id] = {f: getattr(topic, f) for f in TOPIC_FIELDS}
        return topic

    def data(self) -> dict[str, Any]:
        return {
            "ideas": {str(k): v for k, v in self.ideas.items()}, "topics": {str(k): v for k, v in self.topics.items()}, "new_ideas": self.new_ideas, "new_topics": self.new_topics,
            "files": {str(k): v for k, v in self.files.items()}, "new_links": self.new_links,
        }  # fmt: skip


def _record(session: AsyncSession, project_id: int, kind: str, source: str, author: str, summary: dict[str, Any], snap: Snap) -> IdeaChange:
    """Not committed. ``summary["ideas"]``: the ideas it touched, for the UI."""
    summary.setdefault("ideas", sorted(set(snap.ideas) | set(snap.new_ideas)))
    change = IdeaChange(project_id=project_id, kind=kind, source=source, author=author[:100], summary=summary, undo=snap.data())
    session.add(change)
    return change


async def _publish_change(session: AsyncSession, change: IdeaChange) -> None:
    bus.publish(change.project_id, "idea_change.upserted", await change_out(session, change))


def _version(session: AsyncSession, idea: Idea, source: str) -> None:
    session.add(IdeaVersion(project_id=idea.project_id, idea_id=idea.id, title=idea.title, text=idea.text, source=source))


async def undo(session: AsyncSession, change: IdeaChange) -> IdeaChange:
    """Back to the state before ``change``: texts, topics, what was merged or taken. Ideas it created are set
    aside (dismissed, "undone"), never deleted; the replaced texts stay in the versions."""
    if change.undone_at is not None:
        raise ServiceError("This was undone already.", 409)
    data = change.undo or {}
    project_id = change.project_id
    shown_topics: list[IdeaTopic] = []
    for key, fields in (data.get("topics") or {}).items():
        topic = await session.get(IdeaTopic, int(key))
        if topic is None:
            topic = IdeaTopic(id=int(key), project_id=project_id, **fields)
            session.add(topic)
        else:
            for field, value in fields.items():
                setattr(topic, field, value)
        shown_topics.append(topic)
    await session.flush()
    touched: list[Idea] = []
    for key, fields in (data.get("ideas") or {}).items():
        idea = await session.get(Idea, int(key))
        if idea is None or idea.project_id != project_id:
            continue  # deleted by a person since
        if (idea.title, idea.text) != (fields.get("title"), fields.get("text")):
            _version(session, idea, "undo")
        for field, value in fields.items():
            if field == "closed_at":
                value = datetime.fromisoformat(value) if value else None
            elif field in ("topic_id", "merged_into_id", "task_id") and value is not None:
                table = {"topic_id": IdeaTopic, "merged_into_id": Idea, "task_id": Task}[field]
                value = value if await session.get(table, value) is not None else None
            setattr(idea, field, value)
        touched.append(idea)
    for key, idea_id in (data.get("files") or {}).items():
        attachment = await session.get(Attachment, int(key))
        if attachment is not None and await session.get(Idea, idea_id) is not None:
            attachment.idea_id = idea_id
    for link_id in data.get("new_links") or []:
        if (link := await session.get(IdeaLink, link_id)) is not None:
            await session.delete(link)
    now = utcnow()
    for idea_id in data.get("new_ideas") or []:
        idea = await session.get(Idea, idea_id)
        if idea is not None and idea.status == IdeaStatus.OPEN:
            idea.status, idea.closed_at, idea.closed_reason = IdeaStatus.DISMISSED, now, "undone"
            touched.append(idea)
    await session.flush()
    gone: list[int] = []
    for topic_id in data.get("new_topics") or []:
        topic = await session.get(IdeaTopic, topic_id)
        used = await session.scalar(select(Idea.id).where(Idea.topic_id == topic_id, Idea.status == IdeaStatus.OPEN).limit(1))
        if topic is not None and not topic.manual and used is None:
            gone.append(topic.id)
            await session.delete(topic)
    change.undone_at = now
    await session.commit()
    for topic_id in gone:
        bus.publish(project_id, "idea_topic.deleted", {"id": topic_id})
    for topic in shown_topics:
        publish_topic(topic)
    await publish(session, project_id, touched)
    await _publish_change(session, change)
    return change


# --- what a person does ------------------------------------------------------------------------


async def create(session: AsyncSession, project_id: int, data: IdeaCreate, author: str) -> Idea:
    """An idea as typed, without the model reading it first (``orchestration.ideas.Ideas.read_input`` does)."""
    if data.topic_id is not None:
        await get_topic(session, project_id, data.topic_id)
    placed = data.topic_id is not None or data.x is not None or data.y is not None
    idea = Idea(
        project_id=project_id, title=data.title.strip(), text=data.text.strip(), author=author[:100], topic_id=data.topic_id, x=data.x, y=data.y,
        topic_manual=placed, sort_state=PENDING,
    )  # fmt: skip
    session.add(idea)
    await session.commit()
    await publish(session, project_id, [idea])
    return idea


async def receive(session: AsyncSession, project_id: int, text: str, files: list[tuple[str, str, BinaryIO]], author: str) -> Idea:
    """What a person typed, kept at once with its files: waiting to be read (status reading) by the model in the
    background (``orchestration.ideas.Ideas.read_soon``), in the order it came. Files alone become an idea named
    after them, not read; so does a long text (``READ_TEXT``), as written."""
    text = text.replace("\r\n", "\n").strip()
    if len(text) > IDEA_TEXT_MAX:
        raise ServiceError(f"The text is too long: at most {IDEA_TEXT_MAX:,} characters.", 422)
    idea = Idea(project_id=project_id, text=text or ", ".join(name for name, _kind, _data in files), author=author[:100], sort_state=PENDING)
    idea.status = IdeaStatus.READING if text and len(text) <= READ_TEXT else IdeaStatus.OPEN
    session.add(idea)
    await session.commit()
    for name, kind, data in files:
        await board_service.store_attachment(session, project_id, name, kind, data, idea_id=idea.id)
    await session.refresh(idea)
    await publish(session, project_id, [idea])
    return idea


async def waiting_input(session: AsyncSession, project_id: int) -> Idea | None:
    """The oldest input still to be read."""
    query = select(Idea).where(Idea.project_id == project_id, Idea.status == IdeaStatus.READING).order_by(Idea.id).limit(1)
    return (await session.scalars(query)).first()


async def keep_unread(session: AsyncSession, idea: Idea) -> None:
    """Reading it broke: the text stays as written, an idea left unsorted ("rearrange" sorts it)."""
    idea.status, idea.title, idea.sort_state = IdeaStatus.OPEN, "", FAILED
    await session.commit()
    await publish(session, idea.project_id, [idea])


async def update(session: AsyncSession, idea: Idea, fields: dict[str, Any], author: str) -> tuple[Idea, IdeaChange | None]:
    """A person's change. A new title or text keeps the old one as a version, can be undone, and has the idea
    sorted again. Moving it (topic or place) pins it: the model leaves its topic alone from then on."""
    if fields.get("topic_id") is not None:
        await get_topic(session, idea.project_id, fields["topic_id"])
    title = fields.pop("title", None)
    text = fields.pop("text", None)
    title = title.strip() if title is not None else idea.title
    text = text.strip() if text is not None else idea.text
    change = None
    if (title, text) != (idea.title, idea.text):
        snap = Snap()
        before = {"title": idea.title, "text": idea.text}
        _set_text(session, snap, idea, title, text, "user")
        change = _record(session, idea.project_id, "edit", "user", author, {"idea_id": idea.id, "before": before, "after": {"title": title, "text": text}}, snap)
    if {"topic_id", "x", "y"} & set(fields):
        idea.topic_manual = True
        if "topic_id" in fields and idea.sort_state != SORTED and change is None:
            idea.sort_state = SORTED
    for key, value in fields.items():
        setattr(idea, key, value)
    await session.commit()
    await publish(session, idea.project_id, [idea])
    if change is not None:
        await _publish_change(session, change)
    return idea, change


async def restore_version(session: AsyncSession, idea: Idea, version_id: int, author: str) -> tuple[Idea, IdeaChange | None]:
    version = await session.get(IdeaVersion, version_id)
    if version is None or version.idea_id != idea.id:
        raise NotFound("Version not found")
    return await update(session, idea, {"title": version.title, "text": version.text}, author)


async def attachments(session: AsyncSession, idea: Idea) -> list[Attachment]:
    return list((await session.scalars(select(Attachment).where(Attachment.idea_id == idea.id).order_by(Attachment.id))).all())


async def add_attachments(session: AsyncSession, idea: Idea, files: list[tuple[str, str, bytes | BinaryIO]]) -> list[Attachment]:
    """Files attached to an idea (name, type, content), stored like a ticket's. Closing the idea keeps them,
    the retention never deletes them (services/retention.py only looks at tickets' files)."""
    added = [await board_service.store_attachment(session, idea.project_id, name, kind, data, idea_id=idea.id) for name, kind, data in files]
    await publish(session, idea.project_id, [idea])
    return added


async def remove(session: AsyncSession, idea: Idea) -> None:
    """Deleted on purpose by a person: gone for good, with its links, versions and files."""
    project_id, idea_id = idea.project_id, idea.id
    others = await _linked(session, [idea_id])
    for attachment in await attachments(session, idea):
        board_service.storage_path(attachment).unlink(missing_ok=True)
        thumbnail_service.discard(attachment.storage_name)
        await session.delete(attachment)
    if await search_schema.available(session):  # out of the search at once, not with the indexer's sweep
        await session.execute(delete(SearchDocument).where(SearchDocument.project_id == project_id, SearchDocument.kind == "idea", SearchDocument.source_id == str(idea_id)))
    await session.delete(idea)
    await session.commit()
    bus.publish(project_id, "idea.deleted", {"id": idea_id})
    await _publish_linked(session, project_id, [idea_id], others)


async def withdraw_question(session: AsyncSession, idea: Idea) -> Idea:
    if idea.question_status != QUESTION_OPEN:
        raise ServiceError("This idea has no open question.", 409)
    idea.question_status = QUESTION_WITHDRAWN
    await session.commit()
    await publish(session, idea.project_id, [idea])
    return idea


async def take_answer(session: AsyncSession, idea: Idea, option_index: int | None, text: str) -> str:
    """The person's answer to the model's question about the idea, in words. The model then works it into the idea."""
    text = text.strip()
    if idea.question_status != QUESTION_OPEN:
        raise ServiceError("This idea has no open question.", 409)
    if option_index is None and not text:
        raise ServiceError("Pick an option or write an answer.")
    if option_index is not None and option_index >= len(idea.question_options):
        raise ServiceError("Unknown option.")
    parts = [idea.question_options[option_index]["label"]] if option_index is not None else []
    answer = " — ".join(parts + ([text] if text else []))
    idea.question_status, idea.question_answer = QUESTION_ANSWERED, answer
    await session.commit()
    await publish(session, idea.project_id, [idea])
    return answer


def conflict_choice(idea: Idea, option_index: int | None) -> tuple[str, int, int] | None:
    """What an option of a conflict question does (``_conflict``): ("keep", kept, left out) or ("both", a, b)."""
    if idea.question_status != QUESTION_OPEN or option_index is None or not 0 <= option_index < len(idea.question_options):
        return None
    action, *ids = str(idea.question_options[option_index].get("resolve") or "").split(":")
    numbers = [n for n in (_int(i) for i in ids) if n is not None]
    return (action, numbers[0], numbers[1]) if action in ("keep", "both") and len(numbers) == 2 else None


async def _settle_conflicts(session: AsyncSession, idea: Idea) -> list[int]:
    """The person answered about ``idea``: its conflicts are decided (``RESOLVED``). Flushed; the new links' ids."""
    query = select(IdeaLink).where(or_(IdeaLink.idea_id == idea.id, IdeaLink.other_id == idea.id), IdeaLink.kind.in_((CONFLICT, RESOLVED)))
    links = list((await session.scalars(query)).all())
    decided = {frozenset((link.idea_id, link.other_id)) for link in links if link.kind == RESOLVED}
    made: list[IdeaLink] = []
    for link in links:
        if link.kind == CONFLICT and (pair := frozenset((link.idea_id, link.other_id))) not in decided:
            decided.add(pair)
            made.append(IdeaLink(project_id=idea.project_id, idea_id=link.idea_id, other_id=link.other_id, kind=RESOLVED))
    session.add_all(made)
    await session.flush()
    return [link.id for link in made]


async def resolve_conflict(session: AsyncSession, idea: Idea, option_index: int, author: str) -> IdeaChange:
    """A conflict question answered with one of its options, without the model: keep one (the other is left out,
    kept with the reason) or both (they no longer count as a contradiction). One change: undone, the question is
    open again and so is the idea left out."""
    choice = conflict_choice(idea, option_index)
    if choice is None:
        raise ServiceError("This option decides no conflict.")
    action, first, second = choice
    project_id = idea.project_id
    snap = Snap()
    snap.idea(idea)
    idea.question_status, idea.question_answer = QUESTION_ANSWERED, idea.question_options[option_index]["label"]
    summary: dict[str, Any] = {"idea_id": idea.id, "choice": action, "ideas": sorted({idea.id, first, second})}
    gone: list[int] = []
    if action == "keep" and (dropped := await session.get(Idea, second)) is not None and dropped.project_id == project_id and dropped.status == IdeaStatus.OPEN:
        snap.idea(dropped)
        dropped.status, dropped.closed_at, dropped.closed_reason = IdeaStatus.DISMISSED, utcnow(), f"contradicted i{first}: the person kept that one"
        summary.update(kept=first, dropped=[second])
        await session.flush()
        gone = await _drop_empty_topics(session, project_id, snap)
    snap.new_links += await _settle_conflicts(session, idea)
    change = _record(session, project_id, "resolve", "answer", author, summary, snap)
    await session.commit()
    for topic_id in gone:
        bus.publish(project_id, "idea_topic.deleted", {"id": topic_id})
    await publish(session, project_id, list((await session.scalars(select(Idea).where(Idea.id.in_(summary["ideas"])))).all()))
    await _publish_linked(session, project_id, summary["ideas"])
    await _publish_change(session, change)
    return change


async def _free_color(session: AsyncSession, project_id: int) -> int:
    used = list((await session.scalars(select(IdeaTopic.color).where(IdeaTopic.project_id == project_id))).all())
    return next((c for c in range(COLORS) if c not in used), len(used) % COLORS)


async def create_topic(session: AsyncSession, project_id: int, data: IdeaTopicIn) -> IdeaTopic:
    if not data.name:
        raise ServiceError("A topic needs a name.")
    color = data.color if data.color is not None else await _free_color(session, project_id)
    topic = IdeaTopic(project_id=project_id, name=data.name.strip(), color=color, x=data.x, y=data.y, manual=True)
    session.add(topic)
    await session.commit()
    publish_topic(topic)
    return topic


async def update_topic(session: AsyncSession, topic: IdeaTopic, data: IdeaTopicIn) -> IdeaTopic:
    """Renamed by a person: the model keeps the name from then on."""
    fields = data.model_dump(exclude_unset=True)
    if fields.get("name"):
        fields["name"] = fields["name"].strip()
        topic.manual = topic.manual or fields["name"] != topic.name
    for key, value in fields.items():
        if value is not None or key in ("x", "y"):
            setattr(topic, key, value)
    await session.commit()
    publish_topic(topic)
    return topic


async def remove_topic(session: AsyncSession, topic: IdeaTopic) -> list[Idea]:
    """Its ideas stay, unsorted (sorted into other topics next). Returns its open ideas."""
    project_id, topic_id = topic.project_id, topic.id
    ideas = list((await session.scalars(select(Idea).where(Idea.topic_id == topic_id))).all())
    for idea in ideas:
        idea.topic_id, idea.topic_manual = None, False
    await session.delete(topic)
    await session.commit()
    bus.publish(project_id, "idea_topic.deleted", {"id": topic_id})
    left = [i for i in ideas if i.status == IdeaStatus.OPEN]
    await publish(session, project_id, left)
    return left


async def _drop_empty_topics(session: AsyncSession, project_id: int, snap: Snap | None = None) -> list[int]:
    """Topics without an open idea that no person made or named; not committed."""
    used = select(Idea.topic_id).where(Idea.project_id == project_id, Idea.status == IdeaStatus.OPEN, Idea.topic_id.is_not(None))
    query = select(IdeaTopic).where(IdeaTopic.project_id == project_id, IdeaTopic.manual.is_(False), IdeaTopic.id.not_in(used))
    empty = list((await session.scalars(query)).all())
    for topic in empty:
        if snap is not None:
            snap.topic(topic)
        await session.delete(topic)
    return [t.id for t in empty]


# --- the model's answers -----------------------------------------------------------------------


def parse_json(text: str | None) -> dict[str, Any]:
    """The JSON object in a model's answer: also inside a code fence, with words around it, or with trailing
    commas. ValueError if there is none."""
    text = (text or "").strip()
    candidates = [m.group(1) for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S)]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    candidates.append(text)
    for candidate in candidates:
        for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                value = json.loads(attempt)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("no JSON object in the answer")


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and (digits := value.strip().lstrip("#iI")).isdigit():
        return int(digits)
    return None


def _ints(value: Any) -> list[int]:
    return [n for n in (_int(v) for v in (value if isinstance(value, list) else [value])) if n is not None]


def _str(value: Any, limit: int) -> str:
    return truncate(value.strip(), limit) if isinstance(value, str) else ""


def _options(value: Any) -> list[dict[str, str]]:
    found = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str) and item.strip():
            found.append({"label": _str(item, 100), "description": ""})
        elif isinstance(item, dict) and _str(item.get("label"), 100):
            found.append({"label": _str(item.get("label"), 100), "description": _str(item.get("description"), 300)})
    return found[:4]


def _idea_line(idea: Idea, limit: int) -> str:
    text = " ".join(idea.text.split())
    head = f"{idea.title.strip()}: " if idea.title.strip() else ""
    return f"i{idea.id} {truncate(head + text, limit)}"


def _project_head(project: Project) -> str:
    return f"Project: {project.name}" + (f"\n{truncate(project.description, 1000)}" if project.description.strip() else "")


def _set_text(session: AsyncSession, snap: Snap, idea: Idea, title: str, text: str, source: str) -> None:
    """A new title and text; the old ones are kept as a version. The idea is sorted again."""
    snap.idea(idea)
    _version(session, idea, source)
    idea.title, idea.text, idea.sort_state = truncate(title, 200), text, PENDING


async def _merge_into(session: AsyncSession, snap: Snap, ideas: list[Idea], target: Idea) -> None:
    """``ideas`` merged into ``target`` (which has the text of all of them by now): kept, closed, pointing to it.
    Their files go to ``target``, and so do their links to other open ideas, each of its kind (to be merged,
    kept apart, a conflict). Not committed."""
    ids = [i.id for i in ideas if i.id != target.id]
    if not ids:
        return
    now = utcnow()
    for idea in ideas:
        if idea.id != target.id:
            snap.idea(idea)
            idea.status, idea.merged_into_id, idea.closed_at, idea.closed_reason = IdeaStatus.MERGED, target.id, now, f"merged into i{target.id}"
    for attachment in await session.scalars(select(Attachment).where(Attachment.idea_id.in_(ids))):
        snap.files.setdefault(attachment.id, attachment.idea_id)
        attachment.idea_id = target.id
    links = (await session.scalars(select(IdeaLink).where(or_(IdeaLink.idea_id.in_(ids), IdeaLink.other_id.in_(ids)), IdeaLink.kind != SIMILAR))).all()
    others = {link.other_id if link.idea_id in ids else link.idea_id for link in links} - {target.id, *ids}
    still_open = set((await session.scalars(select(Idea.id).where(Idea.id.in_(others), Idea.status == IdeaStatus.OPEN))).all()) if others else set()
    query = select(IdeaLink.idea_id, IdeaLink.other_id, IdeaLink.kind).where(or_(IdeaLink.idea_id == target.id, IdeaLink.other_id == target.id))
    have = {(frozenset((a, b)), kind) for a, b, kind in (await session.execute(query)).all()}
    made: list[IdeaLink] = []
    for link in links:
        other = link.other_id if link.idea_id in ids else link.idea_id
        if other in still_open and (frozenset((other, target.id)), link.kind) not in have:
            have.add((frozenset((other, target.id)), link.kind))
            pair = (target.id, other) if link.idea_id in ids else (other, target.id)  # a duplicate keeps its direction
            made.append(IdeaLink(project_id=target.project_id, idea_id=pair[0], other_id=pair[1], kind=link.kind))
    session.add_all(made)
    await session.flush()
    snap.new_links += [link.id for link in made]


async def _new_idea(session: AsyncSession, snap: Snap, project_id: int, title: str, text: str, author: str) -> Idea:
    idea = Idea(project_id=project_id, title=truncate(title, 200), text=text, author=author[:100], sort_state=PENDING)
    session.add(idea)
    await session.flush()
    snap.new_ideas.append(idea.id)
    return idea


# --- reading an input --------------------------------------------------------------------------

INTENT_SYSTEM = """\
You read what a person typed into the board of ideas of a software project and decide what they mean. You \
change nothing yourself; ideas are only collected here, nothing is built.

Answer with one JSON object and nothing else, one of:
{"action": "new", "ideas": [{"title": "...", "text": "..."}]}
  A new idea. Several entries in "ideas" if the text holds several independent ideas, each with the \
person's words for it.
{"action": "amend", "target": 12, "title": "...", "text": "..."}
  The text changes or adds to idea 12: its whole new title and text. Keep everything it said unless the \
person asks to drop it.
{"action": "merge", "ids": [3, 7], "title": "...", "text": "..."}
  The person asks to put these ideas together: the whole title and text of the merged idea.
{"action": "question", "question": "...", "options": [{"label": "...", "description": "..."}]}
  Only if you cannot tell at all what is meant: a short question with 2-4 options, the recommended first.

Only amend or merge when the person clearly means that existing idea. When in doubt, make it a new idea: \
never overwrite an idea silently; a new idea that belongs to an existing one is merged with it later. Keep the \
person's words and language: take their text over nearly as written. A title has 2-6 words. Ideas are named \
by id (i12).
"""

RESOLVE_NOTE = """\
Idea i{id} was unclear, so the person was asked: {question}
Their answer: {answer}
Work the answer into the ideas: "amend" i{id} with it, or "merge" it with the idea it belongs to (ids \
including {id}), or "new" with several ideas if it splits up (the first replaces i{id}), or, only if the \
answer says to leave ideas out (e.g. of two that contradict each other, keep one), \
{{"action": "drop", "ids": [7], "reason": "..."}}. Do not ask again."""


async def intent_prompt(session: AsyncSession, project: Project, text: str, draft: Idea | None = None, answer: str = "") -> str:
    """All open ideas on a small board; on a big one (``BOARD_WHOLE``) those closest to the text, the newest ones
    and, for an answer, the ideas linked to the one asked about."""
    ideas = await open_ideas(session, project.id)
    names = {t.id: t.name for t in await topics(session, project.id)}
    whole = ({draft.id} | await _linked(session, [draft.id])) if draft is not None else set()  # e.g. asked whether to merge: nothing may get lost
    parts = [_project_head(project)]
    head = "Ideas:"
    if len(ideas) > BOARD_WHOLE:
        about = text if draft is None else f"{draft.title}\n{draft.text}\n{draft.question}\n{answer}"
        near, _how = await neighbours.near_text(session, project, about, ideas, INTENT_NEAR)
        shown = set(near) | {i.id for i in ideas[-INTENT_RECENT:]} | whole
        head = f"Ideas (of {len(ideas)}: the closest to it and the newest):"
        ideas = [i for i in ideas if i.id in shown]
    if ideas:
        parts.append(head + "\n" + "\n".join(f"{_idea_line(i, MERGE_TEXT if i.id in whole else 300)} [{names.get(i.topic_id, 'no topic')}]" for i in ideas))
    if draft is not None:
        parts.append(RESOLVE_NOTE.format(id=draft.id, question=draft.question, answer=answer))
    else:
        parts.append(f"The person typed:\n{text}")
    return "\n\n".join(parts)


async def apply_intent(
    session: AsyncSession, project_id: int, data: dict[str, Any] | None, text: str, author: str, *, draft: Idea | None = None, typed: Idea | None = None
) -> IdeaChange:
    """Do what the model read in ``text``, or (``draft``) what it made of the answer to its question about
    that idea. An answer it got wrong, or none, never loses the text: it becomes a new idea, or the answer is
    added to the draft. ``typed``: the input as it was kept while waiting (``receive``); it becomes the (first)
    new idea, or goes with its files into the idea it amends or merges into."""
    data = data if isinstance(data, dict) else {}
    waiting = [typed] if typed is not None else []

    async def new_idea(title: str, text: str) -> Idea:
        if not waiting:
            return await _new_idea(session, snap, project_id, title, text, author)
        idea = waiting.pop()
        idea.status, idea.title, idea.text, idea.sort_state = IdeaStatus.OPEN, truncate(title, 200), text, PENDING
        snap.new_ideas.append(idea.id)
        return idea

    action = data.get("action")
    by_id = {i.id: i for i in await open_ideas(session, project_id)}
    if draft is not None:
        by_id[draft.id] = draft
    snap = Snap()
    summary: dict[str, Any] = {"input": text}
    kind = ""
    title, new_text = _str(data.get("title"), 200), data.get("text").strip() if isinstance(data.get("text"), str) else ""
    if action == "amend" and (target := by_id.get(_int(data.get("target")) or -1)) is not None and new_text:
        summary.update(idea_id=target.id, before={"title": target.title, "text": target.text}, after={"title": title or target.title, "text": new_text})
        _set_text(session, snap, target, title or target.title, new_text, "ai")
        if draft is not None and draft.id != target.id:
            await _merge_into(session, snap, [draft], target)
            summary["merged"] = [draft.id]
        kind = "amend"
    elif action == "merge" and new_text:
        ids = [i for i in dict.fromkeys(_ints(data.get("ids"))) if i in by_id]
        if draft is not None and draft.id not in ids:
            ids.append(draft.id)
        if len(ids) >= 2:
            target = by_id[ids[0]]
            summary.update(idea_id=target.id, merged=ids[1:], before={str(i): {"title": by_id[i].title, "text": by_id[i].text} for i in ids}, after={"title": title or target.title, "text": new_text})
            _set_text(session, snap, target, title or target.title, new_text, "ai")
            await _merge_into(session, snap, [by_id[i] for i in ids[1:]], target)
            kind = "merge"
    elif action in ("new", "several"):
        items = [(_str(i.get("title"), 200), i["text"].strip()) for i in data.get("ideas") or [] if isinstance(i, dict) and isinstance(i.get("text"), str) and i["text"].strip()]
        if items:
            made: list[Idea] = []
            for n, (item_title, item_text) in enumerate(items[:20]):
                if n == 0 and draft is not None:
                    summary.update(idea_id=draft.id, before={"title": draft.title, "text": draft.text}, after={"title": item_title or draft.title, "text": item_text})
                    _set_text(session, snap, draft, item_title or draft.title, item_text, "ai")
                    made.append(draft)
                else:
                    made.append(await new_idea(item_title, item_text))
            summary.update(created=snap.new_ideas[:])
            kind = "several" if len(made) > 1 else "new"
    elif action == "drop" and draft is not None:
        dropped = [by_id[i] for i in dict.fromkeys(_ints(data.get("ids"))) if i in by_id]
        reason = _str(data.get("reason"), 200) or "left out after the person's answer"
        now = utcnow()
        for idea in dropped:
            snap.idea(idea)
            idea.status, idea.closed_at, idea.closed_reason = IdeaStatus.DISMISSED, now, truncate(f"{reason} (answer to i{draft.id})", 300)
        if dropped:
            summary.update(idea_id=draft.id, dropped=[i.id for i in dropped], ideas=sorted({draft.id, *(i.id for i in dropped)}))
            kind = "resolve"
    elif action == "question" and draft is None and (question := _str(data.get("question"), 500)) and (options := _options(data.get("options"))):
        idea = await new_idea("", text)
        idea.question, idea.question_options, idea.question_status, idea.question_at = question, options, QUESTION_OPEN, utcnow()
        summary.update(created=[idea.id], question=question)
        kind = "question"
    if not kind:  # nothing usable: keep what the person wrote
        summary["fallback"] = True
        if draft is not None:
            summary.update(idea_id=draft.id, before={"title": draft.title, "text": draft.text})
            _set_text(session, snap, draft, draft.title, f"{draft.text}\n\n{draft.question}\n→ {text}", "ai")
            summary["after"] = {"title": draft.title, "text": draft.text}
            kind = "amend"
        else:
            idea = await new_idea("", text)
            summary["created"] = [idea.id]
            kind = "new"
    if draft is not None:  # answered: what it contradicted is decided
        snap.new_links += await _settle_conflicts(session, draft)
    if typed is not None:
        summary["input_id"] = typed.id
    if waiting:  # amended or merged into an idea: the input goes there, with its files (an undo keeps them there)
        target_id = summary["idea_id"]
        typed.status, typed.merged_into_id, typed.closed_at, typed.closed_reason = IdeaStatus.MERGED, target_id, utcnow(), f"read into i{target_id}"
        for attachment in await session.scalars(select(Attachment).where(Attachment.idea_id == typed.id)):
            attachment.idea_id = target_id
    change = _record(session, project_id, kind, "answer" if draft is not None else "input", author, summary, snap)
    await session.commit()
    touched = [i for i in (await session.scalars(select(Idea).where(Idea.id.in_([*change.summary["ideas"], *(i.id for i in waiting)])))).all()]
    await _publish_linked(session, project_id, [i.id for i in touched])
    await publish(session, project_id, touched)
    await _publish_change(session, change)
    return change


# --- sorting -----------------------------------------------------------------------------------

SORT_SYSTEM = """\
You sort the ideas of a software project into topics, for a board of ideas, and keep the ideas themselves \
clear: one idea per card, nothing said twice, contradictions shown. You only sort: you plan and start nothing.

Answer with one JSON object and nothing else:
{"topics": [{"name": "...", "hint": "...", "ideas": [12, 15]}],
 "rename": [{"topic": "...", "name": "..."}],
 "ideas": [{"id": 12, "merge_with": null, "split": [], "conflict": null, "hint": "...", "question": null}],
 "unsure": false}

- "topics": every idea under "Sort these" in exactly one topic, by id; never leave one without a topic. An \
existing topic if it fits (its name exactly as written), else a new one: one to three words, in the language of \
the ideas. A short hint per topic, e.g. "3 ideas about payment". You may also move ideas already sorted to a \
better topic, as many at once as needed, by listing their ids there.
- "rename" (optional, else []): existing topics to rename. A topic renamed to the name of another existing one \
is merged into it.
- "ideas": only ideas with something more to say, else leave them out:
  - "merge_with": the id of another idea that says substantially the same, or that this one only adds to or \
details, so that both belong in one idea. They are then merged into one, nothing of either is lost. Not for \
ideas that only touch the same area: those share a topic. Else null.
  - "split": only if the idea holds several independent ideas, each of which could be planned on its own: its \
parts, [{"title": "...", "text": "..."}], with every word of the idea in one of them, as written. The first \
part stays this idea. Else [].
  - "conflict": if the idea contradicts another one (they want opposite things, both cannot be built as they \
are): {"with": 5, "question": "..."}, a short question to the person about what they want, in the language \
of the ideas. The person decides; never merge such ideas. Else null.
  - "hint": a few words for the person, only where there is something to say, e.g. an open point. Else "".
  - "question": rarely, only if an idea is so unclear that it can neither be sorted nor later planned: \
{"question": "...", "options": [{"label": "...", "description": "..."}]}, 2-4 options, the recommended \
first. At most one question per answer.
- "unsure": true if ideas fit no topic well or the topics should be rearranged.
- Ids: only ids from this message.
"""


def unsorted(ideas: list[Idea]) -> list[Idea]:
    """Open ideas still to be sorted: new or changed ones (pending), and those left without a topic or unsorted
    (sorting failed, the model gave none, their topic was removed); not one a person took out of its topic."""
    return [i for i in ideas if i.sort_state == PENDING or (not i.topic_manual and (i.sort_state == FAILED or i.topic_id is None))]


def sort_prompt(project: Project, existing: list[IdeaTopic], others: list[Idea], batch: list[Idea], *, total: int | None = None) -> str:
    """``others``: all other open ideas, or (``total``: how many the board has) those near the batch."""
    names = {t.id: t.name for t in existing}
    parts = [_project_head(project)]
    if existing:
        parts.append("Topics:\n" + "\n".join(f"- {t.name}" + (f" ({t.hint})" if t.hint else "") for t in existing))
    if others and total is None:
        parts.append("Ideas already sorted:\n" + "\n".join(f"{_idea_line(i, 200)} [{names.get(i.topic_id, 'no topic')}]" for i in others))
    elif others:
        parts.append(f"Ideas closest to these (of {total} on the board):\n" + "\n".join(f"{_idea_line(i, NEAR_TEXT)} [{names.get(i.topic_id, 'no topic')}]" for i in others))
    parts.append("Sort these:\n" + "\n".join(_idea_line(i, SORT_TEXT) + (" [asked before: no question]" if i.question_status else "") for i in batch))
    return "\n\n".join(parts)


async def sort_request(session: AsyncSession, project: Project, ideas: list[Idea], batch: list[Idea]) -> str:
    """The prompt to sort ``batch`` of the open ``ideas``: with all others on a small board, else with the
    closest ones of each (``NEIGHBOURS``, ``NEARBY_MAX`` in all), so its size does not grow with the board."""
    batch_ids = {i.id for i in batch}
    others = [i for i in ideas if i.id not in batch_ids]
    existing = await topics(session, project.id)
    if len(ideas) <= BOARD_WHOLE:
        return sort_prompt(project, existing, others, batch)
    near, _how = await neighbours.around(session, project, batch, others, NEIGHBOURS)
    by_id = {i.id: i for i in others}
    shown = neighbours.spread([near.get(i.id, []) for i in batch], NEARBY_MAX)
    return sort_prompt(project, existing, [by_id[i] for i in sorted(shown)], batch, total=len(ideas))


async def sort_failed(session: AsyncSession, project_id: int, ids: list[int]) -> None:
    ideas = list((await session.scalars(select(Idea).where(Idea.id.in_(ids), Idea.sort_state == PENDING))).all())
    for idea in ideas:
        idea.sort_state = FAILED
    await session.commit()
    await publish(session, project_id, ideas)


async def apply_sort(session: AsyncSession, project_id: int, ids: list[int], data: dict[str, Any]) -> tuple[int, bool]:
    """Store what the model answered for the ideas ``ids``: topics (by groups of ideas, or per idea as earlier
    prompts asked), ideas to merge (``merge_request`` merges them), conflicts (a question to the person, both
    marked), splits (each an undoable change). Renamed or merged topics and ideas already sorted that it moved are
    one undoable change of kind reorder. What it got wrong is left out: an idea it gave no topic stays unsorted
    (failed, sorted again next time), a link to an id it made up is not made, an idea a person placed keeps its
    topic. Returns how many it sorted and whether it was unsure."""
    batch = {i.id: i for i in (await session.scalars(select(Idea).where(Idea.id.in_(ids), Idea.status == IdeaStatus.OPEN))).all()}
    others = {i.id: i for i in await open_ideas(session, project_id) if i.id not in batch}
    valid = set(batch) | set(others)
    existing = await topics(session, project_id)
    old_names = {t.id: t.name for t in existing}
    by_name = {t.name.casefold(): t for t in existing}
    touched: dict[int, IdeaTopic] = {}
    unsure, asked = False, False
    snap = Snap()  # what it rearranged besides sorting: renamed and merged topics, ideas moved
    renamed: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []

    async def topic_named(name: str) -> IdeaTopic:
        topic = by_name.get(name.casefold())
        if topic is None:
            topic = IdeaTopic(project_id=project_id, name=name, color=await _free_color(session, project_id))
            session.add(topic)
            await session.flush()
            by_name[name.casefold()] = topic
            snap.new_topics.append(topic.id)
        return topic

    def move(idea: Idea, topic: IdeaTopic) -> None:
        if idea.topic_manual or idea.topic_id == topic.id:
            return
        if idea.id in others:
            snap.idea(idea)
            moved.append({"idea_id": idea.id, "title": idea.title or truncate(idea.text, 60), "from": old_names.get(idea.topic_id), "to": topic.name})
        idea.topic_id = topic.id
        touched[topic.id] = topic

    every = {**others, **batch}
    renames = data.get("rename")
    for item in renames if isinstance(renames, list) else []:
        if not isinstance(item, dict) or (old := by_name.get(_str(item.get("topic"), 100).casefold())) is None or old.manual:
            continue
        name = _str(item.get("name"), 100)
        if not name or name == old.name:
            continue
        into = by_name.get(name.casefold())
        if into is not None and into.id != old.id:  # merged into the other one
            for idea in [i for i in every.values() if i.topic_id == old.id]:
                move(idea, into)
            continue
        snap.topic(old)
        renamed.append({"topic_id": old.id, "from": old.name, "to": name})
        del by_name[old.name.casefold()]
        old.name = name
        by_name[name.casefold()] = old
        touched[old.id] = old

    done: set[int] = set()
    placed: set[int] = set()
    groups = data.get("topics")
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict) or not (name := _str(group.get("name"), 100)):
            continue
        members = [i for i in dict.fromkeys(_ints(group.get("ideas"))) if i in valid and i not in placed]
        if members:
            topic = await topic_named(name)
            for idea_id in members:
                placed.add(idea_id)
                move(every[idea_id], topic)
    items = data.get("ideas")
    items = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    for item in items:  # earlier prompts: the topic per idea
        if (idea := batch.get(_int(item.get("id")) or -1)) is not None and idea.id not in placed and (name := _str(item.get("topic"), 100)):
            placed.add(idea.id)
            move(idea, await topic_named(name))

    splits: list[tuple[Idea, list[tuple[str, str]]]] = []
    conflicts: list[tuple[Idea, int, str]] = []
    for item in items:
        if (idea := batch.get(_int(item.get("id")) or -1)) is None or idea.id in done:
            continue
        done.add(idea.id)
        idea.hint = _str(item.get("hint"), 300)
        unsure = unsure or item.get("unsure") is True
        question = item.get("question")
        if not asked and not idea.question_status and isinstance(question, dict) and (text := _str(question.get("question"), 500)) and (options := _options(question.get("options"))):
            idea.question, idea.question_options, idea.question_status, idea.question_at = text, options, QUESTION_OPEN, utcnow()
            asked = True
        conflict = item.get("conflict")
        against = _int(conflict.get("with")) if isinstance(conflict, dict) else None
        if against in valid and against != idea.id:
            conflicts.append((idea, against, _str(conflict.get("question"), 500)))
        merge = _int(item.get("merge_with")) or _int(item.get("duplicate_of"))  # duplicate_of: what earlier prompts asked for
        await session.execute(delete(IdeaLink).where(IdeaLink.idea_id == idea.id, IdeaLink.kind.in_((SIMILAR, DUPLICATE))))
        if merge in valid and merge not in (idea.id, against) and frozenset((idea.id, merge)) not in await _kept_apart(session, [idea.id]):
            session.add(IdeaLink(project_id=project_id, idea_id=idea.id, other_id=merge, kind=DUPLICATE))
        if len(parts := _parts(item.get("split"))) >= 2:
            splits.append((idea, parts))
    unsure = unsure or data.get("unsure") is True
    sorted_ids: set[int] = set()
    for idea in batch.values():
        if idea.id in placed or (idea.id in done and idea.topic_manual):
            idea.sort_state = SORTED
            sorted_ids.add(idea.id)
            if idea.id not in done:  # sorted without more to say: its old marks go
                idea.hint = ""
                await session.execute(delete(IdeaLink).where(IdeaLink.idea_id == idea.id, IdeaLink.kind.in_((SIMILAR, DUPLICATE))))
        else:
            idea.sort_state = FAILED  # no topic from the model: sorted again next time
    for idea, other, question in conflicts:
        await _conflict(session, project_id, idea, other, question)
    split_changes = [c for idea, parts in splits if (c := await _split(session, project_id, idea, parts)) is not None]
    for item in groups if isinstance(groups, list) else []:
        if isinstance(item, dict) and (topic := by_name.get(_str(item.get("name"), 100).casefold())) is not None and isinstance(item.get("hint"), str):
            topic.hint = _str(item["hint"], 300)
            touched[topic.id] = topic
    await session.flush()
    gone: list[int] = []
    change = None
    if renamed or moved:
        gone = await _drop_empty_topics(session, project_id, snap)
        removed = [{"topic_id": t, "name": old_names[t], "into": sorted({m["to"] for m in moved if m["from"] == old_names[t]})} for t in gone if t in old_names]
        summary = {"renamed": renamed, "created": [], "removed": removed, "moved": moved, "ideas": sorted(m["idea_id"] for m in moved)}
        change = _record(session, project_id, "reorder", "auto", "", summary, snap)
    await session.commit()
    for topic_id in gone:
        bus.publish(project_id, "idea_topic.deleted", {"id": topic_id})
    for topic in touched.values():
        if topic.id not in gone:
            publish_topic(topic)
    parts = [i for c in split_changes for i in c.summary["created"]]
    await publish(session, project_id, list(batch.values()) + [others[m["idea_id"]] for m in moved] + list((await session.scalars(select(Idea).where(Idea.id.in_(parts)))).all()))
    await _publish_linked(session, project_id, list(batch))
    for c in [*split_changes, *([change] if change else [])]:
        await _publish_change(session, c)
    return len(sorted_ids), unsure


def _parts(value: Any) -> list[tuple[str, str]]:
    items = value if isinstance(value, list) else []
    return [(_str(p.get("title"), 200), p["text"].strip()) for p in items if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()]


def _title(idea: Idea) -> str:
    return idea.title.strip() or truncate(" ".join(idea.text.split()), 60)


async def _conflict(session: AsyncSession, project_id: int, idea: Idea, other_id: int, question: str) -> None:
    """The model found ``idea`` to contradict another one: both marked (``CONFLICT``), and a question to the person
    on one of them, with what to do as options (``resolve_conflict``) and room for an own answer. Never asked twice
    about the same pair, never resolved without the person. A pending merge of the two is called off. Not committed."""
    pair = select(IdeaLink).where(or_(and_(IdeaLink.idea_id == idea.id, IdeaLink.other_id == other_id), and_(IdeaLink.idea_id == other_id, IdeaLink.other_id == idea.id)))
    links = list((await session.scalars(pair)).all())
    other = await session.get(Idea, other_id)
    if other is None or any(link.kind in (CONFLICT, RESOLVED) for link in links):
        return
    for link in links:
        if link.kind == DUPLICATE:
            await session.delete(link)
    session.add(IdeaLink(project_id=project_id, idea_id=idea.id, other_id=other_id, kind=CONFLICT))
    asked = idea if idea.question_status != QUESTION_OPEN else other if other.question_status != QUESTION_OPEN else None
    if asked is None:
        return  # both have an open question already: the marker shows the conflict
    partner = other if asked is idea else idea
    a, b = _title(asked), _title(partner)
    asked.question = question or truncate(f"“{a}” and “{b}” contradict each other. Which one applies?", 500)
    asked.question_options = [
        {"label": truncate(f"Keep “{a}”", 100), "description": truncate(f"“{b}” is left out", 300), "resolve": f"keep:{asked.id}:{partner.id}"},
        {"label": truncate(f"Keep “{b}”", 100), "description": truncate(f"“{a}” is left out", 300), "resolve": f"keep:{partner.id}:{asked.id}"},
        {"label": "Both, in different contexts", "description": "Both stay; they no longer count as a contradiction", "resolve": f"both:{asked.id}:{partner.id}"},
    ]
    asked.question_status, asked.question_answer, asked.question_at = QUESTION_OPEN, "", utcnow()


async def _split(session: AsyncSession, project_id: int, idea: Idea, parts: list[tuple[str, str]]) -> IdeaChange | None:
    """The idea holds several independent ideas: the first part stays it (its files stay with it), the others
    become new ideas in its topic, sorted next; one undoable change. Not split: an idea a person wrote or placed by
    hand, one with an open question, one whose split was undone, or parts that leave much of its text out."""
    if idea.question_status == QUESTION_OPEN or await _fixed(session, [idea]):
        return None
    if sum(len(text) for _, text in parts) < SPLIT_KEEPS * len(idea.text):
        return None
    undone = await session.scalars(select(IdeaChange.summary).where(IdeaChange.project_id == project_id, IdeaChange.kind == "split", IdeaChange.undone_at.is_not(None)))
    if any(summary.get("idea_id") == idea.id for summary in undone):
        return None
    snap = Snap()
    before = {"title": idea.title, "text": idea.text}
    (title, text), rest = parts[0], parts[1:SPLIT_PARTS]
    _set_text(session, snap, idea, title or idea.title, text, "ai")
    idea.sort_state = SORTED  # what it says now was sorted just now
    made = []
    for part_title, part_text in rest:
        part = await _new_idea(session, snap, project_id, part_title, part_text, idea.author)
        part.topic_id = idea.topic_id
        made.append(part)
    links = [IdeaLink(project_id=project_id, idea_id=part.id, other_id=idea.id, kind=SPLIT) for part in made]
    session.add_all(links)
    await session.flush()
    snap.new_links += [link.id for link in links]
    summary = {"idea_id": idea.id, "created": [p.id for p in made], "before": before, "after": {"title": idea.title, "text": idea.text}}
    return _record(session, project_id, "split", "auto", "", summary, snap)


# --- rearranging the topics --------------------------------------------------------------------

REORDER_SYSTEM = """\
You keep the board of ideas of a software project clear. Rearrange its topics: split a topic that grew \
big or mixes things, merge topics that belong together, rename unclear ones, make a topic where several \
ideas share one, move ideas to where they fit best. A clear overview matters more than keeping things as \
they are. A topic marked (fixed) keeps its name; an idea marked (fixed) stays where it is.

Answer with one JSON object and nothing else:
{"topics": [{"id": 4, "name": "...", "hint": "...", "ideas": [12, 15]}, {"id": null, "name": "...", "hint": "...", "ideas": [3, 8]}]}
- Every idea id exactly once. "id": an existing topic to keep or rename, null for a new one. Existing topics \
you leave out are removed.
- Names have one to three words, in the language of the ideas; "hint" is a short note, e.g. "3 ideas about \
payment". Usually 3 to 10 topics, with 2 or more ideas each.
- "duplicates" (optional, else leave it out): groups of ideas that say the same or belong in one idea, e.g. \
[[5, 9]]. Each group is merged into one idea afterwards, nothing of them is lost. Not for ideas that only \
touch the same thing.
- If "Other topics" are listed, you see only a part of the board: rearrange the topics and ideas shown; you may \
move an idea into one of the other topics by its id; leave the other topics out of your answer otherwise.
"""


def _topic_line(t: IdeaTopic) -> str:
    return f"- id {t.id}: {t.name}" + (" (fixed)" if t.manual else "") + (f" - {t.hint}" if t.hint else "")


def reorder_prompt(project: Project, existing: list[IdeaTopic], ideas: list[Idea], outside: list[IdeaTopic] | None = None) -> str:
    """``outside``: on a big board, the topics not in this group (``reorder_groups``)."""
    parts = [_project_head(project)]
    if existing:
        parts.append("Topics:\n" + "\n".join(_topic_line(t) for t in existing))
    if outside:
        parts.append("Other topics:\n" + "\n".join(_topic_line(t) for t in outside[:OTHER_TOPICS]))
    parts.append("Ideas [topic id]:\n" + "\n".join(f"{_idea_line(i, 250)} [{i.topic_id or 'none'}]" + (" (fixed)" if i.topic_manual else "") for i in ideas))
    return "\n\n".join(parts)


async def reorder_groups(session: AsyncSession, project: Project, focus: set[int | None] | None = None) -> list[tuple[list[int], list[int]]]:
    """What is rearranged together: (topic ids, idea ids) per call. A small board (``BOARD_WHOLE``) at once.
    A big one by groups of related topics of up to ``REORDER_IDEAS`` ideas: two topics are related by how many
    of their ideas are each other's neighbours (services/idea_neighbours.py), the most related join first; a
    topic bigger than that goes alone, its ideas in parts. ``focus``: only the groups around these topics (None:
    the ideas without a topic), after new ideas were sorted in; else all of them (the button)."""
    ideas = await open_ideas(session, project.id)
    if len(ideas) <= BOARD_WHOLE:
        return [([t.id for t in await topics(session, project.id)], [i.id for i in ideas])] if ideas else []
    members: dict[int | None, list[int]] = {}
    for idea in ideas:
        members.setdefault(idea.topic_id, []).append(idea.id)
    topic_of = {i.id: i.topic_id for i in ideas}
    near, _how = await neighbours.around(session, project, ideas, ideas, 3)
    weight: dict[tuple[int | None, int | None], int] = {}
    for idea_id, others in near.items():
        for other in others:
            a, b = topic_of[idea_id], topic_of[other]
            if a != b:
                weight[(a, b)] = weight.get((a, b), 0) + 1
                weight[(b, a)] = weight.get((b, a), 0) + 1
    order = sorted(members, key=lambda t: (-len(members[t]), t or 0))
    seeds = order if focus is None else [t for t in order if t in focus]
    taken: set[int | None] = set()
    groups: list[tuple[list[int], list[int]]] = []
    for seed in seeds:
        if seed in taken:
            continue
        group, size = [seed], len(members[seed])
        taken.add(seed)
        while True:
            fits = [t for t in order if t not in taken and size + len(members[t]) <= REORDER_IDEAS]
            related = [t for t in fits if sum(weight.get((g, t), 0) for g in group)]
            pick = max(related, key=lambda t: sum(weight.get((g, t), 0) for g in group), default=None)
            if pick is None and focus is None and fits:
                pick = fits[-1]  # the button: small unrelated topics go together too, fewer calls
            if pick is None:
                break
            group.append(pick)
            taken.add(pick)
            size += len(members[pick])
        ids = [i for t in group for i in members[t]]
        for start in range(0, len(ids), REORDER_IDEAS):
            groups.append(([t for t in group if t is not None], ids[start : start + REORDER_IDEAS]))
    return groups


async def apply_reorder(session: AsyncSession, project_id: int, data: dict[str, Any], source: str) -> IdeaChange | None:
    """Rearrange the topics as the model proposes, within the rules: a person's topic keeps its name, a
    person's idea stays, an idea the answer forgot keeps its topic, a topic is removed only once it is empty.
    Returns the change (what was split, merged, renamed, moved), or None if nothing changed."""
    ideas = await open_ideas(session, project_id)
    by_id = {i.id: i for i in ideas}
    existing = {t.id: t for t in await topics(session, project_id)}
    old_names = {t.id: t.name for t in existing.values()}
    snap = Snap()
    placed: dict[int, IdeaTopic] = {}
    kept: set[int] = set()
    renamed: list[dict[str, Any]] = []
    new_topics: list[IdeaTopic] = []
    hinted: list[IdeaTopic] = []
    items = data.get("topics")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name, topic_id = _str(item.get("name"), 100), _int(item.get("id"))
        members = [i for i in dict.fromkeys(_ints(item.get("ideas"))) if i in by_id and i not in placed]
        topic = existing.get(topic_id) if topic_id not in kept else None
        if topic is None and name:
            topic = next((t for t in existing.values() if t.id not in kept and t.name.casefold() == name.casefold()), None)
        if topic is not None:
            kept.add(topic.id)
            snap.topic(topic)
            if name and name != topic.name and not topic.manual:
                renamed.append({"topic_id": topic.id, "from": topic.name, "to": name})
                topic.name = name
        elif name and members:
            topic = IdeaTopic(project_id=project_id, name=name, color=await _free_color(session, project_id))
            session.add(topic)
            await session.flush()
            snap.new_topics.append(topic.id)
            new_topics.append(topic)
        else:
            continue
        if isinstance(item.get("hint"), str) and _str(item["hint"], 300) != topic.hint:
            topic.hint = _str(item["hint"], 300)
            hinted.append(topic)
        for idea_id in members:
            placed[idea_id] = topic
    moved: list[dict[str, Any]] = []
    sources: dict[int, set[str]] = {}
    for idea_id, topic in placed.items():
        idea = by_id[idea_id]
        if idea.topic_manual:
            continue
        if idea.topic_id != topic.id:
            snap.idea(idea)
            moved.append({"idea_id": idea.id, "title": idea.title or truncate(idea.text, 60), "from": old_names.get(idea.topic_id), "to": topic.name})
            sources.setdefault(topic.id, set()).add(old_names.get(idea.topic_id) or "")
            idea.topic_id = topic.id
        if idea.sort_state != SORTED:
            snap.idea(idea)
            idea.sort_state = SORTED
    await _mark_duplicates(session, project_id, [[i for i in _ints(group) if i in by_id] for group in data.get("duplicates") or [] if isinstance(group, list)])
    await session.flush()
    destinations: dict[int, set[str]] = {}
    for move in moved:
        for topic_id, name in old_names.items():
            if move["from"] == name:
                destinations.setdefault(topic_id, set()).add(move["to"])
    gone = await _drop_empty_topics(session, project_id, snap)
    removed = [{"topic_id": t, "name": old_names[t], "into": sorted(destinations.get(t, set()))} for t in gone if t in old_names]
    created = [{"topic_id": t.id, "name": t.name, "from": sorted(n for n in sources.get(t.id, set()) if n)} for t in new_topics if t.id not in gone]
    change = None
    if renamed or moved or removed or created:
        summary = {"renamed": renamed, "created": created, "removed": removed, "moved": moved, "ideas": sorted(m["idea_id"] for m in moved)}
        change = _record(session, project_id, "reorder", source, "", summary, snap)
    await session.commit()
    for topic_id in gone:
        bus.publish(project_id, "idea_topic.deleted", {"id": topic_id})
    for topic in {t.id: t for t in [*existing.values(), *new_topics] if t.id not in gone}.values():
        if topic.id in snap.topics or topic in new_topics or topic in hinted:
            publish_topic(topic)
    await publish(session, project_id, [by_id[i] for i in snap.ideas if i in by_id])
    if change is not None:
        await _publish_change(session, change)
    return change


# --- merging duplicates -----------------------------------------------------------------------

MERGE_SYSTEM = """\
Some ideas on the board of ideas of a software project say the same thing or belong in one idea. You put \
them together into one idea. You only write the idea: you plan and start nothing.

Answer with one JSON object and nothing else, one of:
{"title": "...", "text": "..."}
  The merged idea: a title of 2-6 words and its whole text. The text keeps everything each of them says \
(every detail, wish, example, reason and open point) and says it once. Keep the persons' own words and \
language: take their sentences over as written and leave out only what is said twice.
{"question": "...", "options": [{"label": "...", "description": "..."}]}
  Instead, if the message says to ask first, or if they turn out to mean different things: a short question \
to the person whether to merge them, with 2-4 options, the recommended first.
"""


async def _kept_apart(session: AsyncSession, ids: list[int]) -> set[frozenset[int]]:
    """Pairs among links of these ideas that were merged or asked about once, split from one, or contradict each
    other: not merged on their own (again)."""
    query = select(IdeaLink.idea_id, IdeaLink.other_id).where(IdeaLink.kind.in_(APART), or_(IdeaLink.idea_id.in_(ids), IdeaLink.other_id.in_(ids)))
    return {frozenset(pair) for pair in (await session.execute(query)).all()}


async def _mark_duplicates(session: AsyncSession, project_id: int, groups: list[list[int]]) -> None:
    """The model found these groups of open ideas to say the same: linked, to be merged (``merge_request``)."""
    for group in groups:
        group = list(dict.fromkeys(group))
        if len(group) < 2:
            continue
        kept = await _kept_apart(session, group)
        for other in group[1:]:
            if frozenset((group[0], other)) not in kept:
                session.add(IdeaLink(project_id=project_id, idea_id=other, other_id=group[0], kind=DUPLICATE))


async def duplicate_groups(session: AsyncSession, project_id: int) -> list[list[int]]:
    """Open ideas the model found to say the same (duplicate links between them), in groups, each by age."""
    open_ids = select(Idea.id).where(Idea.project_id == project_id, Idea.status == IdeaStatus.OPEN)
    query = select(IdeaLink.idea_id, IdeaLink.other_id).where(
        IdeaLink.project_id == project_id, IdeaLink.kind == DUPLICATE, IdeaLink.idea_id.in_(open_ids), IdeaLink.other_id.in_(open_ids)
    )
    root: dict[int, int] = {}

    def find(n: int) -> int:
        while root.setdefault(n, n) != n:
            n = root[n]
        return n

    for a, b in (await session.execute(query)).all():
        if a != b:
            root[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for n in root:
        groups.setdefault(find(n), []).append(n)
    return [sorted(g)[:MERGE_GROUP] for g in sorted(groups.values(), key=min)]


async def _fixed(session: AsyncSession, ideas: list[Idea]) -> list[Idea]:
    """Those a person placed or whose text a person wrote again: the model does not rewrite them unasked."""
    edited = set((await session.scalars(select(IdeaVersion.idea_id).where(IdeaVersion.idea_id.in_([i.id for i in ideas]), IdeaVersion.source == "user"))).all())
    return [i for i in ideas if i.topic_manual or i.id in edited]


async def merge_request(session: AsyncSession, project: Project, ids: list[int]) -> tuple[str, dict[int, tuple[str, str]]] | None:
    """The prompt to merge these ideas, and what the model saw of each (``apply_merge`` merges only if that
    is still so). None if they are no longer open."""
    ideas = list((await session.scalars(select(Idea).where(Idea.id.in_(ids), Idea.project_id == project.id, Idea.status == IdeaStatus.OPEN).order_by(Idea.id))).all())
    if len(ideas) < 2:
        return None
    parts = [_project_head(project), "These ideas say the same or belong in one:\n" + "\n\n".join(f"i{i.id} {i.title.strip() or '(no title)'}\n{truncate(i.text, MERGE_TEXT)}" for i in ideas)]
    if fixed := await _fixed(session, ideas):
        parts.append(f"A person wrote or placed {', '.join(f'i{i.id}' for i in fixed)} by hand: do not merge, ask first.")
    return "\n\n".join(parts), {i.id: (i.title, i.text) for i in ideas}


def merged_as_written(seen: dict[int, tuple[str, str]]) -> dict[str, str] | None:
    """Ideas too long for the model to write out again (``MERGE_TEXT``): merged without it, their texts one after
    the other under the oldest one's title. None if the model can merge them."""
    if all(len(text) <= MERGE_TEXT for _title, text in seen.values()):
        return None
    first = min(seen)
    return {"title": seen[first][0], "text": "\n\n".join(seen[i][1] for i in sorted(seen))}


async def apply_merge(session: AsyncSession, project_id: int, seen: dict[int, tuple[str, str]], data: dict[str, Any] | None) -> IdeaChange | None:
    """Merge the ideas the model saw (``merge_request``) into the oldest, with the text it wrote: one change,
    undone as one. One a person placed or wrote by hand is not rewritten unasked: the model's question goes to
    the person instead. Afterwards the ideas are kept apart (``KEPT``): an undone merge is not made again.
    Returns the change, or None (asked; or the ideas changed meanwhile or the answer was no use: tried again
    with the next sorting)."""
    ideas = list((await session.scalars(select(Idea).where(Idea.id.in_(list(seen)), Idea.project_id == project_id).order_by(Idea.id))).all())
    if len(ideas) != len(seen) or any(i.status != IdeaStatus.OPEN or (i.title, i.text) != seen[i.id] for i in ideas):
        return None
    data = data if isinstance(data, dict) else {}
    title, text = _str(data.get("title"), 200), data.get("text").strip() if isinstance(data.get("text"), str) else ""
    fixed = await _fixed(session, ideas)
    question, options = _str(data.get("question"), 500), _options(data.get("options"))
    merge = bool(text) and not fixed
    if not merge and not fixed and not (question and len(options) >= 2):
        return None
    if not merge and question and len(options) >= 2:
        asked = next((i for i in reversed(ideas) if i not in fixed and not i.question_status), None) or next((i for i in reversed(ideas) if not i.question_status), None)
        if asked is not None:  # else asked about all of them before: they stay as they are
            asked.question, asked.question_options, asked.question_status, asked.question_at = question, options, QUESTION_OPEN, utcnow()
    target, others = ideas[0], ideas[1:]
    ids = [i.id for i in ideas]
    linked = set()
    for link in (await session.scalars(select(IdeaLink).where(IdeaLink.idea_id.in_(ids), IdeaLink.other_id.in_(ids)))).all():
        link.kind = KEPT  # not part of the change: an undone merge is not made again
        linked |= {link.idea_id, link.other_id} if target.id in (link.idea_id, link.other_id) else set()
    session.add_all([IdeaLink(project_id=project_id, idea_id=i.id, other_id=target.id, kind=KEPT) for i in others if i.id not in linked])
    change, gone = None, []
    if merge:
        snap = Snap()
        before = {str(i.id): {"title": i.title, "text": i.text} for i in ideas}
        _set_text(session, snap, target, title or target.title, text, "ai")
        await _merge_into(session, snap, others, target)
        await session.flush()
        gone = await _drop_empty_topics(session, project_id, snap)
        summary = {"idea_id": target.id, "merged": [i.id for i in others], "before": before, "after": {"title": target.title, "text": target.text}, "duplicates": True}
        change = _record(session, project_id, "merge", "auto", "", summary, snap)
    await session.commit()
    for topic_id in gone:
        bus.publish(project_id, "idea_topic.deleted", {"id": topic_id})
    await publish(session, project_id, ideas)
    await _publish_linked(session, project_id, ids)
    if change is not None:
        await _publish_change(session, change)
    return change


# --- into tickets ------------------------------------------------------------------------------


async def _open_for_taking(session: AsyncSession, project_id: int, ids: list[int]) -> dict[int, Idea]:
    found = {i.id: i for i in (await session.scalars(select(Idea).where(Idea.id.in_(ids), Idea.project_id == project_id))).all()} if ids else {}
    if missing := [i for i in ids if i not in found]:
        raise NotFound(f"Idea {missing[0]} not found")
    if closed := [i for i in found.values() if i.status != IdeaStatus.OPEN]:
        raise ServiceError(f"Idea {closed[0].id} is no longer open: it is {closed[0].status}.", 409)
    return found


async def take_one(session: AsyncSession, idea: Idea, data: IdeaTicketIn) -> Task:
    """One idea straight into a ticket, by a person. The manager reads of it with its next turn."""
    await _open_for_taking(session, idea.project_id, [idea.id])
    first_line = next((line.strip() for line in idea.text.splitlines() if line.strip()), "Idea")
    title = data.title or idea.title or truncate(first_line, 120)
    description = data.description if data.description is not None else idea.text
    (task,) = await board_service.create_tasks(session, idea.project_id, [TaskCreate(title=title, description=description, status=data.status, priority=data.priority, labels=data.labels)])
    idea.status, idea.task_id, idea.closed_at = IdeaStatus.TAKEN, task.id, utcnow()
    manager = await session.scalar(select(Agent).where(Agent.project_id == idea.project_id, Agent.is_manager.is_(True)))
    if manager is not None:
        column = "To do" if data.status == TaskStatus.TODO else "Backlog"
        text = f"The user turned idea i{idea.id} into ticket #{task.number} {truncate(task.title, 80)} in {column}. Nothing needs doing because of this note: the board's usual rules apply."
        session.add(InboxItem(agent_id=manager.id, text=text))  # read with its next turn; costs no turn
    await _close_and_publish(session, idea.project_id, [idea])
    await _files_to_ticket(session, task, [idea])
    return task


HAND_OVER = "Ideas handed over:"  # the manager knows the message by this start (orchestration/prompts.py)


async def hand_over(session: AsyncSession, project_id: int, ids: list[int] | None, message: str) -> tuple[list[Idea], str]:
    """Ideas a person hands to the manager to make tickets of (#163); ``ids`` None: all open ones. Marks them, so
    nobody hands them over twice, and gives the chat message for the manager."""
    ideas = list((await _open_for_taking(session, project_id, list(dict.fromkeys(ids)))).values()) if ids is not None else await open_ideas(session, project_id)
    if not ideas:
        raise ServiceError("There are no open ideas to hand over.", 409)
    now = utcnow()
    for idea in ideas:
        idea.handed_over_at = now
    await session.flush()
    await publish(session, project_id, ideas)
    lines = [f"- i{i.id} {truncate(i.title or i.text.replace(chr(10), ' '), 80)}" for i in sorted(ideas, key=lambda i: i.id)]
    text = HAND_OVER + "\n" + "\n".join(lines)
    if message.strip():
        text += "\n\n" + message.strip()
    return ideas, text


async def take(session: AsyncSession, project_id: int, task: Task, ids: list[int]) -> list[Idea]:
    """The manager made the ticket from these ideas: they leave the board of ideas, kept with their ticket."""
    ideas = await _open_for_taking(session, project_id, list(dict.fromkeys(ids)))
    now = utcnow()
    for idea in ideas.values():
        idea.status, idea.task_id, idea.closed_at = IdeaStatus.TAKEN, task.id, now
    await _close_and_publish(session, project_id, list(ideas.values()))
    await _files_to_ticket(session, task, list(ideas.values()))
    return list(ideas.values())


async def _files_to_ticket(session: AsyncSession, task: Task, ideas: list[Idea]) -> None:
    """The files of ideas taken into a ticket, attached to it as copies (#168): the ticket's retention or its
    deletion may remove them there, the idea keeps its own. A file the ticket has already (same name and size)
    is not added again; purged ones are left out."""
    if not ideas:
        return
    files = (await session.scalars(select(Attachment).where(Attachment.idea_id.in_([i.id for i in ideas]), Attachment.purged_at.is_(None)).order_by(Attachment.id))).all()
    have = {(a.filename, a.size) for a in await board_service.list_attachments(session, task.id)}
    for a in files:
        if (a.filename, a.size) in have:
            continue
        try:
            with board_service.storage_path(a).open("rb") as fh:
                await board_service.add_attachment(session, task, a.filename, a.content_type, fh)
        except (OSError, ServiceError):  # gone from disk, or larger than uploads may be now: the idea still has it
            continue
        have.add((a.filename, a.size))


BRANCH_ON_ITS_WAY = (LandingStatus.WAITING, LandingStatus.TRAIN, LandingStatus.BACK, LandingStatus.STUCK)


async def from_ticket(session: AsyncSession, task: Task, author: str, *, by_manager: bool = False) -> list[Idea]:
    """A ticket of the Backlog back into the ideas (#172). The ideas it was made from open again (no duplicate),
    else it becomes a new idea with its title and description. What the ticket holds beyond those ideas (its
    description, people's comments) is added to the first one as a new version, its files are copied to it.
    The ticket is archived, not deleted: its history, comments and files stay, and it can go back on the board."""
    if task.archived_at is not None or task.status != TaskStatus.BACKLOG:
        raise ServiceError(f"Only tickets in the Backlog go back to the ideas: #{task.number} is {'archived' if task.archived_at else task.status}.", 409)
    if await board_service.open_assignments(session, task.id):
        raise ServiceError(f"#{task.number} has open assignments.", 409)
    if await session.scalar(select(Landing.id).where(Landing.task_id == task.id, Landing.status.in_(BRANCH_ON_ITS_WAY)).limit(1)) is not None:
        raise ServiceError(f"#{task.number} has a branch on its way to landing.", 409)
    project_id = task.project_id
    ideas = list((await session.scalars(select(Idea).where(Idea.project_id == project_id, Idea.task_id == task.id, Idea.status == IdeaStatus.TAKEN).order_by(Idea.id))).all())
    comments = (await session.scalars(select(Comment).where(Comment.task_id == task.id, Comment.author_kind == "user").order_by(Comment.id))).all()
    notes = [f"Comment{f' by {c.author_name}' if c.author_name else ''}: {c.body.strip()}" for c in comments if c.body.strip()]
    description = task.description.strip()
    now = utcnow()
    if ideas:
        known = " ".join(" ".join(f"{i.title} {i.text}".split()) for i in ideas)
        for idea in ideas:  # a description that only goes on from an idea's text: what was added
            rest = description.removeprefix(idea.text.strip()) if idea.text.strip() else description
            if rest != description and (not rest or rest[0].isspace()):
                description = rest.strip()
        extra = ([description] if description and " ".join(description.split()) not in known else []) + notes
        for idea in ideas:
            if idea.topic_id is not None and await session.get(IdeaTopic, idea.topic_id) is None:
                idea.topic_id = None
            idea.status, idea.task_id, idea.closed_at, idea.closed_reason, idea.handed_over_at = IdeaStatus.OPEN, None, None, "", None
            if idea.topic_id is None:
                idea.sort_state = PENDING
        first = ideas[0]
        if extra:
            _version(session, first, "ai" if by_manager else "user")
            first.text = "\n\n".join([first.text.rstrip(), f"From ticket #{task.number} {task.title}:", *extra]).strip()
            first.sort_state = PENDING
    else:
        first = Idea(project_id=project_id, title=task.title.strip()[:200], text="\n\n".join([description, *notes]).strip(), author=author[:100], sort_state=PENDING)
        session.add(first)
        ideas = [first]
    await session.flush()
    task.archived_at = now
    names = ", ".join(f"i{i.id}" for i in ideas)
    session.add(Comment(task_id=task.id, author_kind="system", body=f"Back to the ideas as {names}; the ticket was archived."))
    if not by_manager and (manager := await session.scalar(select(Agent).where(Agent.project_id == project_id, Agent.is_manager.is_(True)))) is not None:
        text = f"The user turned ticket #{task.number} {truncate(task.title, 80)} back into idea(s) {names}; the ticket is archived. Nothing needs doing because of this note."
        session.add(InboxItem(agent_id=manager.id, text=text))  # read with its next turn; costs no turn
    await session.commit()
    have = {(a.filename, a.size) for a in (await session.scalars(select(Attachment).where(Attachment.idea_id.in_([i.id for i in ideas])))).all()}
    for a in await board_service.list_attachments(session, task.id):
        if a.purged_at is not None or (a.filename, a.size) in have:
            continue
        try:
            with board_service.storage_path(a).open("rb") as fh:
                await board_service.store_attachment(session, project_id, a.filename, a.content_type, fh, idea_id=first.id)
        except (OSError, ServiceError):  # gone from disk, or too large now: the archived ticket still has it
            continue
        have.add((a.filename, a.size))
    for idea in ideas:
        await session.refresh(idea)
    await board_service.publish_task(session, task)
    await publish(session, project_id, ideas)
    await _publish_linked(session, project_id, [i.id for i in ideas])
    return ideas


async def leave_out(session: AsyncSession, project_id: int, ids: list[int], reason: str, duplicate_of: int | None = None) -> list[Idea]:
    """Left out on purpose (a duplicate, covered already, dropped by the user): kept, with the reason."""
    ideas = await _open_for_taking(session, project_id, list(dict.fromkeys(ids)))
    if duplicate_of is not None:
        await get(session, project_id, duplicate_of)
    now = utcnow()
    for idea in ideas.values():
        idea.status, idea.closed_at, idea.closed_reason = IdeaStatus.DISMISSED, now, truncate(reason, 300)
        if duplicate_of is not None and duplicate_of != idea.id:
            session.add(IdeaLink(project_id=project_id, idea_id=idea.id, other_id=duplicate_of, kind=DUPLICATE))
    await _close_and_publish(session, project_id, list(ideas.values()))
    return list(ideas.values())


async def _close_and_publish(session: AsyncSession, project_id: int, ideas: list[Idea]) -> None:
    gone = await _drop_empty_topics(session, project_id)
    await session.commit()
    for topic_id in gone:
        bus.publish(project_id, "idea_topic.deleted", {"id": topic_id})
    await publish(session, project_id, ideas)
    await _publish_linked(session, project_id, [i.id for i in ideas])


# --- for the manager (tools/ideas.py) ----------------------------------------------------------


async def count_open(session: AsyncSession, project_id: int) -> int:
    return await session.scalar(select(func.count(Idea.id)).where(Idea.project_id == project_id, Idea.status == IdeaStatus.OPEN)) or 0


LIST_CHARS = 150_000  # one page of list_ideas; never cut within an idea


def _file_lines(files: list[Attachment]) -> list[str]:
    """An idea's files as the agents read them: under /uploads, like a ticket's."""
    return [f"{a.id} {a.filename} ({format_bytes(a.size)}): " + (board_service.purged_text(a) if a.purged_at else board_service.runner_path(a)) for a in files]


async def manager_list(session: AsyncSession, project_id: int, *, topic: str | None, query: str | None, status: str, offset: int = 0) -> str:
    """The ideas in full (title, whole text, topic, links), by topic. Pages by whole ideas: a page ends before
    the idea that would pass ``LIST_CHARS`` and says where the next one starts."""
    stmt = select(Idea).where(Idea.project_id == project_id).order_by(Idea.topic_id, Idea.id)
    if status != "all":
        stmt = stmt.where(Idea.status == status)
    ideas = list((await session.scalars(stmt)).all())
    names = {t.id: t for t in await topics(session, project_id)}
    if topic:
        ideas = [i for i in ideas if i.topic_id in names and topic.casefold() in names[i.topic_id].name.casefold()]
    if query:
        ideas = [i for i in ideas if query.casefold() in f"{i.title} {i.text}".casefold()]
    if not ideas:
        return "No ideas."
    if offset >= len(ideas):
        return f"No ideas at offset {offset} ({len(ideas)} in all)."
    lines: list[str] = []
    size, current, shown = 0, -1, 0
    page = ideas[offset:]
    outs_by_id = {o.id: o for o in await outs(session, page)}
    files: dict[int, list[Attachment]] = {}
    for a in await session.scalars(select(Attachment).where(Attachment.idea_id.in_([i.id for i in page])).order_by(Attachment.id)):
        files.setdefault(a.idea_id, []).append(a)
    for idea in page:
        block: list[str] = []
        if idea.topic_id != current:
            t = names.get(idea.topic_id) if idea.topic_id is not None else None
            block.append(f"## {t.name}" + (f" ({t.hint})" if t.hint else "") if t else "## No topic")
        out = outs_by_id[idea.id]
        marks = [f"similar {', '.join(f'i{s}' for s in out.similar)}"] if out.similar else []
        marks += [f"duplicate of i{out.duplicate_of}"] if out.duplicate_of else []
        marks += [f"contradicts {', '.join(f'i{c}' for c in out.conflicts)}"] if out.conflicts else []
        marks += [idea.status + (f" #{out.task_number}" if out.task_number else "")] if idea.status != IdeaStatus.OPEN else []
        marks += [f"open question: {idea.question}"] if idea.question_status == QUESTION_OPEN else []
        marks += [f"answered: {idea.question} → {idea.question_answer}"] if idea.question_status == QUESTION_ANSWERED else []
        block.append(f"i{idea.id} {idea.title or '(no title)'}" + (f" [{'; '.join(marks)}]" if marks else ""))
        block.append("\n".join(f"  {line}" for line in idea.text.splitlines()) or "  -")
        block += [f"  file: {line}" for line in _file_lines(files.get(idea.id, []))]
        text = "\n".join(block)
        if shown and size + len(text) > LIST_CHARS:
            break
        lines.append(text)
        size, current, shown = size + len(text) + 1, idea.topic_id, shown + 1
    head = f"Ideas {offset + 1}-{offset + shown} of {len(ideas)}:"
    tail = f"\n(continue with offset={offset + shown}: {len(ideas) - offset - shown} more)" if offset + shown < len(ideas) else ""
    return head + "\n" + "\n".join(lines) + tail


async def manager_detail(session: AsyncSession, idea: Idea) -> str:
    (out,) = await outs(session, [idea])
    topic = await session.get(IdeaTopic, idea.topic_id) if idea.topic_id else None
    lines = [f"i{idea.id} {idea.title or '(no title)'} — {idea.status}" + (f" into #{out.task_number}" if out.task_number else "") + (f" (into i{idea.merged_into_id})" if idea.merged_into_id else "")]
    lines.append(f"Topic: {topic.name if topic else '-'}" + (f"; by {idea.author}" if idea.author else ""))
    if out.similar or out.duplicate_of or out.conflicts:
        links = [f"similar i{s}" for s in out.similar] + ([f"duplicate of i{out.duplicate_of}"] if out.duplicate_of else []) + [f"contradicts i{c}" for c in out.conflicts]
        lines.append("Links: " + ", ".join(links))
    if idea.closed_reason:
        lines.append(f"Reason: {idea.closed_reason}")
    if idea.question_status:
        lines.append(f"Question ({idea.question_status}): {idea.question}" + (f" → {idea.question_answer}" if idea.question_answer else ""))
    lines.append(f"\n{idea.text}")
    if files := await attachments(session, idea):
        lines.append("\nAttachments:\n" + "\n".join(f"- {line}" for line in _file_lines(files)))
    older = await versions(session, idea)
    if older:
        lines.append("\nEarlier versions (newest first):\n" + "\n".join(f"- {truncate((v.title + ': ' if v.title else '') + ' '.join(v.text.split()), 300)}" for v in older[:10]))
    return "\n".join(lines)
