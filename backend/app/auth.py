from __future__ import annotations

import logging
import threading

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import Conversation, Resume, Run, User

logger = logging.getLogger("resume_studio.auth")
bearer = HTTPBearer(auto_error=False)

_firebase_lock = threading.Lock()
_firebase_ready = False


def _init_firebase() -> bool:
    global _firebase_ready
    if _firebase_ready:
        return True
    if not settings.firebase_credentials_path and not settings.firebase_project_id:
        return False
    with _firebase_lock:
        if _firebase_ready:
            return True
        try:
            import firebase_admin
            from firebase_admin import credentials

            if not firebase_admin._apps:  # pragma: no cover - external SDK state
                if settings.firebase_credentials_path:
                    cred = credentials.Certificate(settings.firebase_credentials_path)
                else:
                    cred = credentials.ApplicationDefault()
                firebase_admin.initialize_app(cred, {"projectId": settings.firebase_project_id})
            _firebase_ready = True
            return True
        except Exception as exc:  # pragma: no cover - depends on deployment
            logger.error("Firebase initialization failed: %s", type(exc).__name__)
            return False


def _verify_firebase(credentials: HTTPAuthorizationCredentials | None) -> User | None:
    if not credentials or credentials.scheme.lower() != "bearer":
        return None
    try:
        from firebase_admin import auth as firebase_auth

        decoded = firebase_auth.verify_id_token(credentials.credentials, check_revoked=True)
        return User(
            id=decoded["uid"],
            email=decoded.get("email"),
            display_name=decoded.get("name"),
        )
    except Exception as exc:
        logger.info("Firebase token rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired authentication token.")


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
    x_dev_user: str | None = Header(default=None),
) -> User:
    use_firebase = settings.auth_mode == "firebase" or (
        settings.auth_mode == "auto" and (settings.firebase_credentials_path or settings.firebase_project_id)
    )
    if use_firebase:
        firebase_user = _verify_firebase(credentials)
        if not firebase_user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
        user = db.get(User, firebase_user.id)
        if not user:
            user = User(
                id=firebase_user.id,
                email=firebase_user.email,
                display_name=firebase_user.display_name,
            )
            _insert_user(db, user)
        request.state.user_id = user.id
        return user

    # Development / demo identity. Never enabled implicitly when Firebase is configured.
    dev_id = (x_dev_user or "").strip()
    if credentials and credentials.scheme.lower() == "bearer" and credentials.credentials.startswith("dev:"):
        dev_id = credentials.credentials[4:]
    user_id = dev_id or settings.dev_user_id
    if not re_safe_user_id(user_id):
        raise HTTPException(status_code=400, detail="Invalid development user id.")
    user = db.get(User, user_id)
    if not user:
        user = User(id=user_id, email=f"{user_id}@local.dev", display_name=user_id.replace("-", " ").title())
        _insert_user(db, user)
    request.state.user_id = user.id
    return user


def _insert_user(db: Session, user: User) -> None:
    """Insert a first-seen user without failing concurrent first requests.

    Two requests for a brand-new identity can race here; the loser gets an
    IntegrityError on the primary key and simply re-reads the row the winner wrote.
    """
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if not db.get(User, user.id):
            raise
        return
    db.refresh(user)


def re_safe_user_id(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", value or ""))


def owned_resume(db: Session, resume_id: str, user_id: str) -> Resume:
    resume = db.get(Resume, resume_id)
    if not resume or resume.user_id != user_id or resume.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Resume not found.")
    return resume


def owned_conversation(db: Session, conversation_id: str, user_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


def owned_run(db: Session, run_id: str, user_id: str) -> Run:
    run = db.get(Run, run_id)
    if not run or run.user_id != user_id:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run
