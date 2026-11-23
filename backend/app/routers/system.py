from __future__ import annotations

import hashlib
import logging
import shutil

from fastapi import APIRouter, Depends
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import settings
from ..db import get_db
from ..models import (
    Conversation,
    JobDescription,
    Message,
    Proposal,
    Resume,
    ResumeVersion,
    Run,
    RunEvent,
    User,
)

logger = logging.getLogger("resume_studio.system")
router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {
        "user": {"id": user.id, "email": user.email, "display_name": user.display_name},
        "auth_mode": settings.auth_mode,
        "capabilities": {
            "llm": settings.llm_enabled,
            "live_research": settings.research_enabled,
            "model": settings.openai_model if settings.llm_enabled else None,
            "max_upload_mb": settings.max_upload_mb,
            "ocr": _ocr_available(),
        },
        "limits": {
            "max_message_chars": settings.max_jd_chars,
            "max_job_description_chars": settings.max_jd_chars,
        },
    }


def _ocr_available() -> bool:
    try:
        import pytesseract  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


@router.delete("/me")
def delete_my_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Best-effort account/data deletion for the authenticated user."""
    resume_ids = [r.id for r in db.scalars(select(Resume).where(Resume.user_id == user.id)).all()]
    run_ids = [r.id for r in db.scalars(select(Run).where(Run.user_id == user.id)).all()]
    conversation_ids = [c.id for c in db.scalars(select(Conversation).where(Conversation.user_id == user.id)).all()]
    if run_ids:
        db.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
    if resume_ids:
        db.execute(delete(Proposal).where(Proposal.resume_id.in_(resume_ids)))
    db.execute(delete(Run).where(Run.user_id == user.id))
    if conversation_ids:
        db.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
    db.execute(delete(Conversation).where(Conversation.user_id == user.id))
    db.execute(delete(JobDescription).where(JobDescription.user_id == user.id))
    if resume_ids:
        db.execute(delete(ResumeVersion).where(ResumeVersion.resume_id.in_(resume_ids)))
        db.execute(delete(Resume).where(Resume.id.in_(resume_ids)))
    db.delete(user)
    db.commit()
    for base in (settings.uploads_dir / user.id, settings.exports_dir / user.id):
        try:
            shutil.rmtree(base, ignore_errors=True)
        except OSError:
            logger.warning("Could not remove storage shard for user %s", hashlib.sha256(user.id.encode()).hexdigest()[:8])
    return {"deleted": True}
