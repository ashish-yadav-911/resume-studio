"""Optional Firebase Firestore mirror for chat history and resume versions.

The relational database remains the transactional source of truth.  When
Firebase is configured (``FIREBASE_PROJECT_ID`` or
``FIREBASE_CREDENTIALS_PATH``), successful writes are mirrored to Firestore in
a daemon thread so a slow network never blocks or breaks a user request.

Document layout::

    {prefix}_users/{user_id}
    {prefix}_users/{user_id}/conversations/{conversation_id}
    {prefix}_users/{user_id}/conversations/{conversation_id}/messages/{message_id}
    {prefix}_users/{user_id}/resumes/{resume_id}
    {prefix}_users/{user_id}/resumes/{resume_id}/versions/{version_id}

Every write is best-effort and logged without content or PII.  Firestore is
never read back by application logic, so an outage cannot corrupt local state.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from .config import settings
from .models import Conversation, Message, Resume, ResumeVersion

logger = logging.getLogger("resume_studio.firestore")

_lock = threading.Lock()
_client: Any = None
_unavailable_logged = False


def _get_client() -> Any:
    global _client, _unavailable_logged
    if not settings.firebase_sync_enabled:
        return None
    if not (settings.firebase_project_id or settings.firebase_credentials_path):
        return None
    with _lock:
        if _client is not None:
            return _client
        try:
            from firebase_admin import firestore

            from .auth import _init_firebase

            if not _init_firebase():
                return None
            _client = firestore.client()
            logger.info("Firestore mirror enabled for project %s", settings.firebase_project_id)
            return _client
        except Exception as exc:  # pragma: no cover - depends on deployment
            if not _unavailable_logged:
                logger.warning("Firestore mirror unavailable: %s", type(exc).__name__)
                _unavailable_logged = True
            return None


def _run(label: str, write: Callable[[Any], None]) -> None:
    client = _get_client()
    if client is None:
        return

    def task() -> None:
        try:
            write(client)
        except Exception as exc:  # pragma: no cover - network/SDK failures
            logger.warning("Firestore mirror write failed (%s): %s", label, type(exc).__name__)

    threading.Thread(target=task, name=f"firestore:{label}", daemon=True).start()


def _user_doc(client: Any, user_id: str) -> Any:
    return client.collection(f"{settings.firebase_collection_prefix}_users").document(user_id)


def mirror_conversation(conversation: Conversation) -> None:
    payload = {
        "id": conversation.id,
        "resume_id": conversation.resume_id,
        "title": conversation.title,
        "target_company": conversation.target_company,
        "target_role": conversation.target_role,
        "message_count": conversation.message_count,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }

    def write(client: Any) -> None:
        _user_doc(client, conversation.user_id).collection("conversations").document(conversation.id).set(
            payload, merge=True
        )

    _run("conversation", write)


def mirror_message(message: Message, user_id: str) -> None:
    payload = {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "run_id": message.run_id,
        "meta": message.meta or {},
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }

    def write(client: Any) -> None:
        _user_doc(client, user_id).collection("conversations").document(message.conversation_id).collection(
            "messages"
        ).document(message.id).set(payload, merge=True)

    _run("message", write)


def mirror_resume(resume: Resume) -> None:
    payload = {
        "id": resume.id,
        "title": resume.title,
        "original_filename": resume.original_filename,
        "mime_type": resume.mime_type,
        "size_bytes": resume.size_bytes,
        "active_version_id": resume.active_version_id,
        "extraction_meta": resume.extraction_meta or {},
        "created_at": resume.created_at.isoformat() if resume.created_at else None,
        "deleted_at": resume.deleted_at.isoformat() if resume.deleted_at else None,
    }

    def write(client: Any) -> None:
        _user_doc(client, resume.user_id).collection("resumes").document(resume.id).set(payload, merge=True)

    _run("resume", write)


def mirror_version(resume: Resume, version: ResumeVersion) -> None:
    payload = {
        "id": version.id,
        "resume_id": version.resume_id,
        "version_number": version.version_number,
        "parent_version_id": version.parent_version_id,
        "content": version.content,
        "facts": version.facts or [],
        "change_summary": version.change_summary,
        "changed_fields": version.changed_fields or [],
        "agent": version.agent,
        "kind": version.kind,
        "approval_status": version.approval_status,
        "conversation_id": version.conversation_id,
        "run_id": version.run_id,
        "undo_of_version_id": version.undo_of_version_id,
        "undone_version_id": version.undone_version_id,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }

    def write(client: Any) -> None:
        _user_doc(client, resume.user_id).collection("resumes").document(resume.id).collection(
            "versions"
        ).document(version.id).set(payload)

    _run("version", write)


def delete_resume_mirror(resume: Resume) -> None:
    def write(client: Any) -> None:
        _user_doc(client, resume.user_id).collection("resumes").document(resume.id).delete()

    _run("resume_delete", write)
