from __future__ import annotations

import asyncio
import json
import logging
from datetime import timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user, owned_run
from ..db import SessionLocal, get_db
from ..events import TERMINAL_EVENTS, bus, replay_events
from ..firestore_sync import mirror_conversation, mirror_message, mirror_resume, mirror_version
from ..models import Conversation, Message, Proposal, Resume, ResumeVersion, Run, User, utcnow
from ..resume_service import apply_changes, create_version, get_path, supersede_pending_proposals
from ..schemas import ApproveRequest, RejectRequest, ResumeContent
from ..serializers import iso, proposal_out, run_out, version_state, version_to_dict

logger = logging.getLogger("resume_studio.runs")
router = APIRouter(prefix="/runs", tags=["runs"])


def _proposal_for_run(db: Session, run_id: str) -> Proposal | None:
    return db.scalar(select(Proposal).where(Proposal.run_id == run_id))


@router.get("/{run_id}")
def get_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = owned_run(db, run_id, user.id)
    proposal = _proposal_for_run(db, run.id)
    return run_out(run, proposal)


def _format_sse(event: dict) -> str:
    data = json.dumps(event, ensure_ascii=False)
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {data}\n\n"


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = owned_run(db, run_id, user.id)
    if last_event_id and last_event_id.isdigit():
        after_seq = max(after_seq, int(last_event_id))

    queue = await bus.subscribe(run.id)

    async def generator():
        last_seq = after_seq
        try:
            terminal = False
            for event in replay_events(run.id, after_seq):
                last_seq = max(last_seq, event["seq"])
                yield _format_sse(event)
                if event["type"] in TERMINAL_EVENTS:
                    terminal = True
            if terminal:
                yield ": end\n\n"
                return
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except (asyncio.TimeoutError, TimeoutError):
                    yield ": ping\n\n"
                    with SessionLocal() as check:
                        current = check.get(Run, run.id)
                        status = current.status if current else "failed"
                    if status in {"awaiting_approval", "completed", "clarification", "failed", "rejected", "approved", "superseded"}:
                        # Drain any persisted tail that the subscriber may have missed.
                        for tail in replay_events(run.id, last_seq):
                            last_seq = max(last_seq, tail["seq"])
                            yield _format_sse(tail)
                        yield ": end\n\n"
                        break
                    continue
                if event["seq"] <= last_seq:
                    continue
                last_seq = event["seq"]
                yield _format_sse(event)
                if event["type"] in TERMINAL_EVENTS:
                    break
        except asyncio.CancelledError:
            raise
        finally:
            await bus.unsubscribe(run.id, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/approve")
async def approve_run(
    run_id: str,
    payload: ApproveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = owned_run(db, run_id, user.id)
    proposal = _proposal_for_run(db, run.id)
    if not proposal:
        raise HTTPException(status_code=404, detail="No proposal exists for this run.")
    resume = db.get(Resume, proposal.resume_id)
    if not resume or resume.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Resume not found.")

    if proposal.status in {"approved", "partially_approved"} and proposal.created_version_id:
        version = db.get(ResumeVersion, proposal.created_version_id)
        return {
            "idempotent": True,
            "proposal": proposal_out(proposal, include_candidate=False),
            "version": version_to_dict(version, include_content=True, active=version.id == resume.active_version_id) if version else None,
            **version_state(db, resume),
        }
    if proposal.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"This proposal is {proposal.status} and can no longer be approved.",
        )
    expected = payload.expected_base_version_id or proposal.base_version_id
    if expected != proposal.base_version_id or resume.active_version_id != proposal.base_version_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_PROPOSAL",
                "message": "The resume changed after this proposal was generated. Request a fresh proposal.",
                "active_version_id": resume.active_version_id,
                "proposal_base_version_id": proposal.base_version_id,
            },
        )

    changes = proposal.changes or []
    proposed = [c for c in changes if c.get("status") == "proposed"]
    proposed_by_id = {c.get("id"): c for c in proposed if c.get("id")}
    if payload.accepted_change_ids is None:
        selected = proposed
    else:
        requested = list(dict.fromkeys(payload.accepted_change_ids))
        if not requested:
            raise HTTPException(status_code=400, detail="Select at least one change to accept.")
        invalid = [cid for cid in requested if cid not in proposed_by_id]
        blocked = [c for c in changes if c.get("id") in requested and c.get("status") != "proposed"]
        if invalid or blocked:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_CHANGE_SELECTION",
                    "message": "Some selected changes are unknown or were blocked by validation.",
                    "invalid_change_ids": invalid,
                    "blocked_change_ids": [c.get("id") for c in blocked],
                },
            )
        selected = [proposed_by_id[cid] for cid in requested]

    if not selected:
        raise HTTPException(status_code=400, detail="No valid changes were selected.")

    base_version = db.get(ResumeVersion, proposal.base_version_id)
    if not base_version:
        raise HTTPException(status_code=409, detail="The base version no longer exists.")

    seen_paths: set[str] = set()
    for change in selected:
        path = change.get("field_path", "")
        operation = (change.get("operation") or "replace").lower()
        if operation == "add":
            # Several additions may target the same list; only guard against duplicates.
            try:
                target_list = get_path(base_version.content, path)
            except KeyError:
                raise HTTPException(status_code=409, detail=f"Change target no longer exists: {path}")
            if not isinstance(target_list, list):
                raise HTTPException(status_code=409, detail=f"Change target is not a list: {path}")
            addition = (change.get("after") or "").strip()
            if any(isinstance(item, str) and item.strip() == addition for item in target_list):
                raise HTTPException(status_code=409, detail=f"'{addition}' is already present in {path}.")
            continue
        if path in seen_paths:
            raise HTTPException(status_code=409, detail=f"Duplicate change path in proposal: {path}")
        seen_paths.add(path)
        try:
            current_value = get_path(base_version.content, path)
        except KeyError:
            raise HTTPException(status_code=409, detail=f"Change target no longer exists: {path}")
        if operation == "remove":
            if not isinstance(current_value, str) or current_value.strip() != (change.get("before") or current_value).strip():
                raise HTTPException(
                    status_code=409,
                    detail=f"The item to remove at {path} changed. Request a fresh proposal.",
                )
            continue
        if not isinstance(current_value, str) or current_value.strip() != (change.get("before") or "").strip():
            raise HTTPException(
                status_code=409,
                detail=f"The base text for {path} changed. Request a fresh proposal.",
            )

    try:
        candidate = apply_changes(base_version.content, selected)
        ResumeContent.model_validate(candidate)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Applying the selected changes produced an invalid resume: {exc}") from exc

    was_localized = any(c.get("agent") == "translation" for c in selected)
    agents = sorted({c.get("agent") or "section_enhancement" for c in selected})
    version = create_version(
        db,
        resume,
        candidate,
        summary=f"Accepted {len(selected)} of {len(proposed)} proposed change(s)",
        changed_fields=[c["field_path"] for c in selected],
        agent=", ".join(agents),
        kind="localized" if was_localized else "accepted",
        parent_version_id=proposal.base_version_id,
        conversation_id=run.conversation_id,
        run_id=run.id,
        commit=False,
    )
    proposal.status = "approved" if len(selected) == len(proposed) else "partially_approved"
    proposal.decided_at = utcnow()
    proposal.created_version_id = version.id
    run.status = "approved"
    run.updated_at = utcnow()
    other_pending = db.scalars(
        select(Proposal).where(
            Proposal.resume_id == resume.id,
            Proposal.status == "pending",
            Proposal.id != proposal.id,
        )
    ).all()
    for other in other_pending:
        other.status = "superseded"
    message = Message(
        conversation_id=run.conversation_id,
        role="assistant",
        content=(
            f"Approved {len(selected)} change(s) and created version v{version.version_number}. "
            "The proposal remains available in your version history."
        ),
        run_id=None,
        meta={"action": "proposal.approved", "proposal_id": proposal.id, "version_id": version.id},
    )
    db.add(message)
    db.commit()
    db.refresh(version)
    db.refresh(message)
    mirror_version(resume, version)
    mirror_resume(resume)
    mirror_message(message, user.id)
    conversation = db.get(Conversation, run.conversation_id)
    if conversation:
        mirror_conversation(conversation)
    await bus.emit(
        db, run.id, "proposal.approved",
        {
            "proposal_id": proposal.id,
            "version_id": version.id,
            "version_number": version.version_number,
            "accepted": len(selected),
            "status": proposal.status,
        },
    )
    return {
        "idempotent": False,
        "proposal": proposal_out(proposal, include_candidate=False),
        "version": version_to_dict(version, include_content=True, active=True),
        **version_state(db, resume),
    }


@router.post("/{run_id}/reject")
async def reject_run(
    run_id: str,
    payload: RejectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = owned_run(db, run_id, user.id)
    proposal = _proposal_for_run(db, run.id)
    if not proposal:
        raise HTTPException(status_code=404, detail="No proposal exists for this run.")
    if proposal.status == "rejected":
        return {"idempotent": True, "proposal": proposal_out(proposal, include_candidate=False)}
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail=f"This proposal is {proposal.status}.")
    proposal.status = "rejected"
    proposal.decided_at = utcnow()
    run.status = "rejected"
    run.updated_at = utcnow()
    message = Message(
        conversation_id=run.conversation_id,
        role="assistant",
        content="No changes were saved. Your active resume version is unchanged.",
        run_id=None,
        meta={"action": "proposal.rejected", "proposal_id": proposal.id, "reason": payload.reason[:500]},
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    mirror_message(message, user.id)
    conversation = db.get(Conversation, run.conversation_id)
    if conversation:
        mirror_conversation(conversation)
    await bus.emit(db, run.id, "proposal.rejected", {"proposal_id": proposal.id, "status": "rejected"})
    return {"idempotent": False, "proposal": proposal_out(proposal, include_candidate=False)}
