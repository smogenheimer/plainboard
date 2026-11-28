"""A team member's own notes. They are always in that member's prompt, so they survive context
resets and compression; a fixed budget for all notes of a member keeps that prompt small.

A full budget never blocks saving: a note a member (or the manager for it) saves pushes out that member's
oldest notes it wrote itself, and the caller gets their text back to merge what still matters. Notes from
the user are never pushed out; a note the user saves may go over the budget."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..events import bus
from ..models import Agent, AgentNote, utcnow
from ..schemas import AgentNoteOut
from .errors import NotFound, ServiceError

BUDGET_CHARS = 3000  # all notes of one member together (~750 tokens)
REVIEW_AFTER_DAYS = 3  # a note unchanged this long is put up for review
ASK_AGAIN_DAYS = 7  # a note the member kept is put up again after this long


async def for_agent(session: AsyncSession, agent_id: int) -> list[AgentNote]:
    return list((await session.scalars(select(AgentNote).where(AgentNote.agent_id == agent_id).order_by(AgentNote.id))).all())


def used(notes: list[AgentNote]) -> int:
    return sum(len(n.text) for n in notes)


async def get(session: AsyncSession, note_id: int, agent_id: int | None = None) -> AgentNote:
    note = await session.get(AgentNote, note_id)
    if note is None or (agent_id is not None and note.agent_id != agent_id):
        raise NotFound(f"Own note {note_id} not found")
    return note


async def save(
    session: AsyncSession, agent: Agent, text: str, *, note: AgentNote | None = None, by_agent_id: int | None = None
) -> tuple[AgentNote, list[AgentNote]]:
    """Adds a note, or replaces the text of ``note``. Returns it and the notes pushed out to make room."""
    text = text.strip()
    if not text:
        raise ServiceError("The note is empty")
    others = [n for n in await for_agent(session, agent.id) if note is None or n.id != note.id]
    over = used(others) + len(text) - BUDGET_CHARS
    pushed_out: list[AgentNote] = []
    if by_agent_id is not None:  # the user's own writes push nothing out
        for old in sorted((n for n in others if n.created_by_agent_id is not None), key=lambda n: (n.updated_at, n.id)):
            if over <= 0:
                break
            pushed_out.append(old)
            over -= len(old.text)
    for old in pushed_out:
        await session.delete(old)
    if note is None:
        note = AgentNote(project_id=agent.project_id, agent_id=agent.id, text=text, created_by_agent_id=by_agent_id)
        session.add(note)
    else:
        note.text = text
    await session.commit()
    if pushed_out:
        bus.publish(note.project_id, "ownnote.deleted", {"ids": [n.id for n in pushed_out]})
    bus.publish(note.project_id, "ownnote.upserted", AgentNoteOut.model_validate(note))
    return note, pushed_out


def pushed_out_text(pushed_out: list[AgentNote], whose: str = "your", how: str = "remember with note_id") -> str:
    """What a caller is told about notes ``save`` pushed out (empty if none)."""
    if not pushed_out:
        return ""
    listed = "\n".join(f"- [{n.id}] {n.text.strip()}" for n in pushed_out)
    return (
        f"\nTo make room, {whose} oldest own notes were removed:\n{listed}\n"
        f"If one still matters, merge it, shortened, into another note ({how})."
    )


async def delete(session: AsyncSession, notes: list[AgentNote]) -> None:
    if not notes:
        return
    project_id, ids = notes[0].project_id, [n.id for n in notes]
    for note in notes:
        await session.delete(note)
    await session.commit()
    bus.publish(project_id, "ownnote.deleted", {"ids": ids})


async def counts(session: AsyncSession, agent_ids: list[int]) -> dict[int, int]:
    if not agent_ids:
        return {}
    q = select(AgentNote.agent_id, func.count()).where(AgentNote.agent_id.in_(agent_ids)).group_by(AgentNote.agent_id)
    return dict((await session.execute(q)).all())


async def take_due_for_review(session: AsyncSession) -> dict[int, list[AgentNote]]:
    """Old notes a member wrote itself, per member, for a reminder in its next turn. Notes from the user
    are never put up for review: only the user removes them."""
    now = utcnow()
    cutoff = now - timedelta(days=REVIEW_AFTER_DAYS)
    q = (
        select(AgentNote)
        .join(Agent, Agent.id == AgentNote.agent_id)
        .where(
            Agent.enabled,
            AgentNote.created_by_agent_id.is_not(None),
            AgentNote.updated_at < cutoff,
            or_(AgentNote.review_asked_at.is_(None), AgentNote.review_asked_at < now - timedelta(days=ASK_AGAIN_DAYS)),
        )
        .order_by(AgentNote.agent_id, AgentNote.id)
    )
    due: dict[int, list[AgentNote]] = {}
    for note in await session.scalars(q):
        note.review_asked_at = now
        due.setdefault(note.agent_id, []).append(note)
    if due:
        await session.commit()
    return due
