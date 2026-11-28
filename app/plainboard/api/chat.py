"""Agent conversations, runs and context controls."""

from __future__ import annotations

from fastapi import APIRouter, File, Request, UploadFile
from sqlalchemy import select

from ..models import Agent, Project, Run, RunStatus, RunTrigger
from ..schemas import AttachmentOut, ChatMessageIn, ConversationOut, MessageOut, RunOut
from ..services import accounts as account_service
from ..services import agents as agent_service
from ..services import board as board_service
from ..services import mentions as mention_service
from ..services import settings as settings_service
from ..services import users as user_service
from ..services.errors import NotFound, ServiceError
from .deps import Runs, Session, Who

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/agents/{agent_id}/conversation", response_model=ConversationOut)
async def get_conversation(agent_id: int, session: Session, before_id: int | None = None, limit: int = 60) -> ConversationOut:
    agent = await agent_service.get(session, agent_id)
    conv = await agent_service.conversation_for(session, agent_id)
    limit = max(1, min(limit, 200))
    rows = await agent_service.messages(session, conv, before_id=before_id, limit=limit + 1)
    out = ConversationOut.model_validate(conv)
    out.context_limit_tokens = agent.context_limit_tokens
    out.has_summary = bool(conv.summary)
    out.has_more = len(rows) > limit
    out.messages = [MessageOut.model_validate(m) for m in reversed(rows[:limit])]
    return out


@router.post("/agents/{agent_id}/messages", response_model=RunOut, status_code=202)
async def send_message(agent_id: int, body: ChatMessageIn, request: Request, session: Session, runs: Runs, who: Who) -> RunOut:
    agent = await agent_service.get(session, agent_id)
    if not agent.is_manager and not await settings_service.get(session, "member_chat"):
        raise ServiceError("Direct messages to members are off. Talk to the manager, or enable it in Settings.", 403)
    if body.client_key and (run := await runs.sent_chat(session, agent.id, body.client_key)):
        return RunOut.model_validate(run)  # it arrived before; the answer did not
    several = bool(await user_service.author(session, who.user, agent.project_id))
    run, new = await runs.chat_once(session, agent, body.text, body.attachment_ids, author=who.name or None, several=several, key=body.client_key)
    if not new:  # an earlier try was still on its way and took it
        return RunOut.model_validate(run)
    await mention_service.record(session, agent.project_id, body.text, kind="chat", author=who.name, author_user_id=who.user.id if who.user else None, agent_id=agent.id)
    if await session.scalar(select(Project.limit_paused_at).where(Project.id == agent.project_id)):
        request.app.state.limit_pause.recheck_soon()  # a person who writes may just have topped up a plan
    return RunOut.model_validate(run)


@router.delete("/runs/{run_id}/message", status_code=204)
async def withdraw_message(run_id: int, session: Session, runs: Runs, part: int | None = None) -> None:
    """Delete a chat message the agent has not received yet. ``part``: one of several waiting for the same turn."""
    await runs.withdraw_chat(session, run_id, part)


@router.post("/agents/{agent_id}/attachments",response_model=list[AttachmentOut], status_code=201)
async def upload(agent_id: int, session: Session, files: list[UploadFile] = File(...)) -> list[AttachmentOut]:
    """Store files for a chat message; send their ids with the message."""
    agent = await agent_service.get(session, agent_id)
    out = []
    for file in files:
        attachment = await board_service.store_attachment(session, agent.project_id, file.filename or "file", file.content_type or "", file.file)
        out.append(AttachmentOut.model_validate(attachment))
    return out


@router.post("/agents/{agent_id}/compress")
async def compress(agent_id: int, session: Session, runs: Runs) -> dict[str, bool]:
    agent = await agent_service.get(session, agent_id)
    if await session.scalar(select(Run.id).where(Run.agent_id == agent_id, Run.status == RunStatus.RUNNING)):
        raise ServiceError("Agent is working; try again when it is idle")
    conv = await agent_service.conversation_for(session, agent_id)
    return {"compressed": await runs.context.compress(session, agent, conv)}


@router.post("/agents/{agent_id}/reset-session")
async def reset_session(agent_id: int, session: Session) -> dict[str, bool]:
    """Start a fresh provider session next turn (memory summary + recent messages are kept)."""
    agent = await agent_service.get(session, agent_id)
    conv = await agent_service.conversation_for(session, agent_id)
    conv.provider_session_id = None
    conv.context_tokens = 0
    await session.commit()
    await agent_service.publish(session, agent)
    return {"reset": True}


@router.get("/projects/{project_id}/runs", response_model=list[RunOut])
async def list_runs(project_id: int, session: Session, agent_id: int | None = None, active: bool = False, limit: int = 50) -> list[RunOut]:
    q = select(Run).where(Run.project_id == project_id)
    if agent_id:
        q = q.where(Run.agent_id == agent_id)
    if active:
        q = q.where(Run.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)))
    rows = (await session.scalars(q.order_by(Run.id.desc()).limit(min(limit, 200)))).all()
    out = [RunOut.model_validate(r) for r in rows]
    waits: dict[int, dict | None] = {}
    for run in out:
        if run.status == RunStatus.QUEUED and run.trigger != RunTrigger.CHAT and run.parent_run_id is None:
            if run.agent_id not in waits:
                agent = await session.get(Agent, run.agent_id)
                waits[run.agent_id] = await account_service.limit_wait(session, agent) if agent else None
            run.limit_wait = waits[run.agent_id]
    return out


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, session: Session, runs: Runs) -> dict[str, bool]:
    if await session.get(Run, run_id) is None:
        raise NotFound("Run not found")
    return {"cancelled": await runs.cancel_run(run_id)}
