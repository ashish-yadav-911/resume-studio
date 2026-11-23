from __future__ import annotations

from datetime import timezone
from typing import Any

from .models import Conversation, Message, Proposal, Resume, ResumeVersion, Run
from .resume_service import can_redo, can_undo, version_to_dict


def iso(value) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def resume_summary(resume: Resume, version_count: int = 0) -> dict[str, Any]:
    return {
        "id": resume.id,
        "title": resume.title,
        "original_filename": resume.original_filename,
        "active_version_id": resume.active_version_id,
        "version_count": version_count,
        "created_at": iso(resume.created_at),
    }


def resume_out(resume: Resume, version: ResumeVersion | None, include_content: bool = True) -> dict[str, Any]:
    return {
        "id": resume.id,
        "title": resume.title,
        "original_filename": resume.original_filename,
        "mime_type": resume.mime_type,
        "size_bytes": resume.size_bytes,
        "extraction_meta": resume.extraction_meta or {},
        "active_version_id": resume.active_version_id,
        "active_version": version_to_dict(
            version, include_content=include_content, active=True
        ) if version else None,
        "created_at": iso(resume.created_at),
    }


def conversation_out(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "resume_id": conversation.resume_id,
        "target_company": conversation.target_company,
        "target_role": conversation.target_role,
        "message_count": conversation.message_count,
        "updated_at": iso(conversation.updated_at),
    }


def message_out(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "run_id": message.run_id,
        "meta": message.meta or {},
        "created_at": iso(message.created_at),
    }


def proposal_out(proposal: Proposal, include_candidate: bool = True) -> dict[str, Any]:
    data = {
        "id": proposal.id,
        "run_id": proposal.run_id,
        "resume_id": proposal.resume_id,
        "base_version_id": proposal.base_version_id,
        "changes": proposal.changes or [],
        "validation": proposal.validation or {},
        "summary": proposal.summary,
        "status": proposal.status,
        "created_version_id": proposal.created_version_id,
        "created_at": iso(proposal.created_at),
    }
    if include_candidate:
        data["candidate_content"] = proposal.candidate_content
    return data


def run_out(run: Run, proposal: Proposal | None = None) -> dict[str, Any]:
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "resume_id": run.resume_id,
        "base_version_id": run.base_version_id,
        "intent": run.intent,
        "status": run.status,
        "plan": run.plan or {},
        "agent_outputs": run.agent_outputs or {},
        "final_response": run.final_response,
        "clarification_question": run.clarification_question,
        "error": run.error,
        "created_at": iso(run.created_at),
        "updated_at": iso(run.updated_at),
        "proposal": proposal_out(proposal) if proposal else None,
    }


def version_state(db, resume: Resume) -> dict[str, bool]:
    return {"can_undo": can_undo(db, resume), "can_redo": can_redo(db, resume)}
