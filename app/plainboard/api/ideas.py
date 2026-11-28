"""A project's ideas: collect, sort, rearrange, undo (services/ideas.py, orchestration/ideas.py).
Members change them, viewers read (services/access.py). Tickets come from them through the manager."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from ..schemas import AttachmentOut, IdeaAnswer, IdeaChangeOut, IDEA_TEXT_MAX, IdeaCreate, IdeaHandOver, IdeaHandOverOut, IdeaOut, IdeasOut, IdeaTicketIn, IdeaTicketOut, IdeaTopicIn, IdeaTopicOut, IdeaUpdate, IdeaVersionOut
from ..models import IdeaStatus
from ..services import agents as agent_service
from ..services import board as board_service
from ..services import ideas as idea_service
from ..services import projects as project_service
from ..services import users as user_service
from ..services.errors import ServiceError
from .deps import Runs, Session, Who

router = APIRouter(prefix="/api/projects/{project_id}/ideas", tags=["ideas"])


async def _out(session, idea) -> IdeaOut:  # noqa: ANN001
    (out,) = await idea_service.outs(session, [idea])
    return out


@router.get("", response_model=IdeasOut)
async def list_ideas(project_id: int, session: Session, runs: Runs, closed: bool = False) -> IdeasOut:
    """The open ideas and their topics; ``closed``: also the ones taken into tickets, left out or merged."""
    project = await project_service.get(session, project_id)
    if not runs.ideas.reading(project_id) and await idea_service.waiting_input(session, project_id) is not None:
        runs.ideas.read_soon(project_id)  # left waiting by a restart
    topics = [IdeaTopicOut.model_validate(t) for t in await idea_service.topics(session, project_id)]
    listed = await idea_service.listed(session, project_id, closed=closed)
    blocked = await runs.ideas.blocked(session, project)
    runs.ideas.sort_left(project_id, {i.id for i in idea_service.unsorted([i for i in listed if i.status == IdeaStatus.OPEN])}, blocked is not None)
    ideas = await idea_service.outs(session, listed)
    return IdeasOut(topics=topics, ideas=ideas, sorting=runs.ideas.sorting(project_id), model_blocked=blocked)


@router.get("/questions", response_model=list[IdeaOut])
async def open_questions(project_id: int, session: Session) -> list[IdeaOut]:
    """The model's open questions about ideas, for "for you"."""
    return await idea_service.outs(session, await idea_service.open_questions(session, project_id))


@router.post("", response_model=IdeaOut, status_code=201)
async def create_idea(project_id: int, body: IdeaCreate, session: Session, runs: Runs, who: Who) -> IdeaOut:
    """An idea exactly as typed (the field of the board uses ``/input``)."""
    await project_service.get(session, project_id)
    idea = await idea_service.create(session, project_id, body, who.name)
    runs.ideas.sort_soon(project_id)
    return await _out(session, idea)


@router.post("/input", response_model=IdeaOut, status_code=202)
async def take_input(
    project_id: int, session: Session, runs: Runs, who: Who, text: str = Form("", max_length=2 * IDEA_TEXT_MAX), files: list[UploadFile] = File(default=[])
) -> IdeaOut:
    """What the person typed (and pasted files; a form's line breaks come as two characters each), kept at once and read by the model in the background: the idea
    comes with status reading; what the model made of it comes as an ``idea_change.upserted`` (``summary.input_id``)."""
    await project_service.get(session, project_id)
    if not text.strip() and not files:
        raise ServiceError("Type something or add a file.", 422)
    idea = await idea_service.receive(session, project_id, text, [(f.filename or "file", f.content_type or "", f.file) for f in files], who.name)
    if idea.status == IdeaStatus.READING:
        runs.ideas.read_soon(project_id)
    else:
        runs.ideas.sort_soon(project_id)
    return await _out(session, idea)


@router.post("/sort", status_code=202)
async def rearrange(project_id: int, session: Session, runs: Runs) -> dict:
    """Rearrange all topics now (the button); comes as an ``idea_change.upserted`` of kind reorder, if anything changed."""
    await project_service.get(session, project_id)
    runs.ideas.sort_soon(project_id, reorder=True)
    return {"sorting": True}


@router.get("/changes", response_model=list[IdeaChangeOut])
async def list_changes(project_id: int, session: Session, limit: int = 20) -> list[IdeaChangeOut]:
    return [await idea_service.change_out(session, c) for c in await idea_service.changes(session, project_id, min(max(limit, 1), 100))]


@router.post("/hand-over", response_model=IdeaHandOverOut)
async def hand_over(project_id: int, body: IdeaHandOver, session: Session, runs: Runs, who: Who) -> IdeaHandOverOut:
    """Ideas to the manager (#163): a chat message it makes tickets from; no ids: all open ones."""
    manager = await agent_service.get_manager(session, project_id)
    if not manager.enabled:
        raise ServiceError("The manager is switched off.", 409)
    ideas, text = await idea_service.hand_over(session, project_id, body.idea_ids, body.message)
    outs = await idea_service.outs(session, ideas)
    several = bool(await user_service.author(session, who.user, project_id))
    await runs.chat(session, manager, text, author=who.name or None, several=several)
    return IdeaHandOverOut(agent_id=manager.id, ideas=outs)


@router.post("/changes/{change_id}/undo", response_model=IdeaChangeOut)
async def undo_change(project_id: int, change_id: int, session: Session) -> IdeaChangeOut:
    change = await idea_service.undo(session, await idea_service.get_change(session, project_id, change_id))
    return await idea_service.change_out(session, change, with_ideas=True)


@router.post("/topics", response_model=IdeaTopicOut, status_code=201)
async def create_topic(project_id: int, body: IdeaTopicIn, session: Session) -> IdeaTopicOut:
    await project_service.get(session, project_id)
    return IdeaTopicOut.model_validate(await idea_service.create_topic(session, project_id, body))


@router.patch("/topics/{topic_id}", response_model=IdeaTopicOut)
async def update_topic(project_id: int, topic_id: int, body: IdeaTopicIn, session: Session) -> IdeaTopicOut:
    topic = await idea_service.get_topic(session, project_id, topic_id)
    return IdeaTopicOut.model_validate(await idea_service.update_topic(session, topic, body))


@router.delete("/topics/{topic_id}", status_code=204)
async def delete_topic(project_id: int, topic_id: int, session: Session, runs: Runs) -> None:
    """Its ideas are sorted into the other topics again."""
    if await idea_service.remove_topic(session, await idea_service.get_topic(session, project_id, topic_id)):
        runs.ideas.sort_soon(project_id)


@router.patch("/{idea_id}", response_model=IdeaOut)
async def update_idea(project_id: int, idea_id: int, body: IdeaUpdate, session: Session, runs: Runs, who: Who) -> IdeaOut:
    """A new title or text is kept as a version before (undo: the ``idea_change.upserted`` of kind edit) and
    has the idea sorted again; a move pins it."""
    idea = await idea_service.get(session, project_id, idea_id)
    idea, change = await idea_service.update(session, idea, body.model_dump(exclude_unset=True), who.name)
    if change is not None:
        runs.ideas.sort_soon(project_id)
    return await _out(session, idea)


@router.delete("/{idea_id}", status_code=204)
async def delete_idea(project_id: int, idea_id: int, session: Session) -> None:
    await idea_service.remove(session, await idea_service.get(session, project_id, idea_id))


@router.post("/{idea_id}/attachments", response_model=list[AttachmentOut], status_code=201)
async def upload(project_id: int, idea_id: int, session: Session, files: list[UploadFile] = File(...)) -> list[AttachmentOut]:
    """Files attached to the idea; downloaded and deleted at ``/api/attachments/{id}`` like a ticket's."""
    idea = await idea_service.get(session, project_id, idea_id)
    added = await idea_service.add_attachments(session, idea, [(f.filename or "file", f.content_type or "", f.file) for f in files])
    return [AttachmentOut.model_validate(a) for a in added]


@router.get("/{idea_id}/versions", response_model=list[IdeaVersionOut])
async def list_versions(project_id: int, idea_id: int, session: Session) -> list[IdeaVersionOut]:
    """Its earlier titles and texts, newest first."""
    idea = await idea_service.get(session, project_id, idea_id)
    return [IdeaVersionOut.model_validate(v) for v in await idea_service.versions(session, idea)]


@router.post("/{idea_id}/versions/{version_id}/restore", response_model=IdeaOut)
async def restore_version(project_id: int, idea_id: int, version_id: int, session: Session, runs: Runs, who: Who) -> IdeaOut:
    idea = await idea_service.get(session, project_id, idea_id)
    idea, change = await idea_service.restore_version(session, idea, version_id, who.name)
    if change is not None:
        runs.ideas.sort_soon(project_id)
    return await _out(session, idea)


@router.post("/{idea_id}/answer", response_model=IdeaChangeOut)
async def answer_question(project_id: int, idea_id: int, body: IdeaAnswer, session: Session, runs: Runs, who: Who) -> IdeaChangeOut:
    """The answer to the model's question about the idea; the model works it in."""
    project = await project_service.get(session, project_id)
    idea = await idea_service.get(session, project_id, idea_id)
    change = await runs.ideas.answer(session, project, idea, body.option_index, body.text, who.name)
    return await idea_service.change_out(session, change, with_ideas=True)


@router.delete("/{idea_id}/question", response_model=IdeaOut)
async def withdraw_question(project_id: int, idea_id: int, session: Session) -> IdeaOut:
    """Dismiss the model's question unanswered."""
    return await _out(session, await idea_service.withdraw_question(session, await idea_service.get(session, project_id, idea_id)))


@router.post("/{idea_id}/ticket", response_model=IdeaTicketOut, status_code=201)
async def idea_to_ticket(project_id: int, idea_id: int, body: IdeaTicketIn, session: Session) -> IdeaTicketOut:
    """One idea straight into a ticket."""
    idea = await idea_service.get(session, project_id, idea_id)
    task = await idea_service.take_one(session, idea, body)
    (ticket,) = await board_service.task_outs(session, [task])
    return IdeaTicketOut(ticket=ticket, idea=await _out(session, idea))
