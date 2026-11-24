from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user, owned_conversation, owned_resume
from ..config import settings
from ..db import get_db
from ..firestore_sync import mirror_conversation, mirror_message
from ..models import (
    Conversation,
    JobDescription,
    Message,
    Proposal,
    Resume,
    Run,
    User,
    utcnow,
)
from ..agents.job_matching import parse_jd_heuristic
from ..orchestrator import orchestrator
from ..schemas import ChatRequest, ConversationCreate
from ..serializers import conversation_out, iso, message_out

logger = logging.getLogger("resume_studio.conversations")
router = APIRouter(prefix="/conversations", tags=["conversations"])

_RUN_TASKS: set[asyncio.Task] = set()


def _schedule(run_id: str) -> None:
    if os.environ.get("VERCEL"):
        # Serverless instances are frozen as soon as the response is sent, so a task
        # started here would never finish. The client drives execution instead via
        # POST /api/runs/{id}/execute.
        return
    task = asyncio.create_task(orchestrator.execute(run_id), name=f"run:{run_id}")
    _RUN_TASKS.add(task)
    task.add_done_callback(_RUN_TASKS.discard)


@router.post("", status_code=201)
def create_conversation(
    payload: ConversationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.resume_id:
        owned_resume(db, payload.resume_id, user.id)
    conversation = Conversation(
        user_id=user.id,
        resume_id=payload.resume_id,
        title=(payload.title or "New conversation")[:200],
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    mirror_conversation(conversation)
    return conversation_out(conversation)


@router.get("")
def list_conversations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversations = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(100)
    ).all()
    return [conversation_out(c) for c in conversations]


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = owned_conversation(db, conversation_id, user.id)
    return conversation_out(conversation)


@router.get("/{conversation_id}/messages")
def list_messages(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=300),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = owned_conversation(db, conversation_id, user.id)
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    ).all()
    return [message_out(m) for m in messages]


@router.post("/{conversation_id}/messages", status_code=202)
async def post_message(
    conversation_id: str,
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = owned_conversation(db, conversation_id, user.id)
    if len(payload.content) > settings.max_jd_chars:
        raise HTTPException(
            status_code=413,
            detail=f"Message is too long. Limit is {settings.max_jd_chars} characters.",
        )

    resume: Resume | None = None
    resume_id = payload.resume_id or conversation.resume_id
    if resume_id:
        resume = owned_resume(db, resume_id, user.id)
        conversation.resume_id = resume.id
    if payload.resume_id and not resume:
        raise HTTPException(status_code=404, detail="Resume not found.")

    base_version_id = None
    if resume:
        base_version_id = payload.base_version_id or resume.active_version_id
        if payload.base_version_id and payload.base_version_id != resume.active_version_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "STALE_VERSION",
                    "message": "You are viewing an older version. Restore it or reload before requesting changes.",
                    "active_version_id": resume.active_version_id,
                },
            )

    running = db.scalar(
        select(Run)
        .where(Run.conversation_id == conversation.id, Run.status.in_(["queued", "running"]))
        .order_by(Run.created_at.desc())
    )
    if running:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RUN_IN_PROGRESS",
                "message": "A run is already in progress for this conversation.",
                "run_id": running.id,
            },
        )

    # A new request supersedes any pending proposal in this conversation.
    pending_runs = db.scalars(
        select(Run).where(Run.conversation_id == conversation.id, Run.status == "awaiting_approval")
    ).all()
    for pending_run in pending_runs:
        proposal = db.scalar(select(Proposal).where(Proposal.run_id == pending_run.id))
        if proposal and proposal.status == "pending":
            proposal.status = "superseded"
        pending_run.status = "superseded"

    if payload.job_description and payload.job_description.strip():
        jd_text = payload.job_description.strip()[: settings.max_jd_chars]
        parsed = parse_jd_heuristic(jd_text)
        jd = JobDescription(
            user_id=user.id,
            resume_id=resume.id if resume else None,
            title=parsed.title or "Job description",
            company=parsed.company,
            raw_text=jd_text,
            parsed=parsed.model_dump(),
        )
        db.add(jd)
        db.flush()
        conversation.job_description_id = jd.id

    # Name the conversation from the first user prompt instead of leaving "New conversation".
    if not conversation.title or conversation.title == "New conversation":
        conversation.title = (payload.content or "").strip()[:80] or "New conversation"

    message = Message(
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
        meta={"resume_id": resume.id if resume else None},
    )
    db.add(message)
    db.flush()
    conversation.message_count += 1
    conversation.updated_at = utcnow()
    run = Run(
        user_id=user.id,
        conversation_id=conversation.id,
        resume_id=resume.id if resume else None,
        base_version_id=base_version_id,
        status="queued",
        intent="PENDING",
        user_message=payload.content,
        plan={},
        agent_outputs={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    db.refresh(message)
    mirror_message(message, user.id)
    mirror_conversation(conversation)
    _schedule(run.id)
    logger.info("Created run %s for conversation %s", run.id, conversation.id)
    return {
        "run_id": run.id,
        "message": message_out(message),
        "status": run.status,
        "events_url": f"/api/runs/{run.id}/events",
    }


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = owned_conversation(db, conversation_id, user.id)
    db.delete(conversation)
    db.commit()
    return {"deleted": True, "conversation_id": conversation_id}
