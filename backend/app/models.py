from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("res"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120))
    storage_path: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(64), index=True)
    active_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list["ResumeVersion"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan", order_by="ResumeVersion.version_number"
    )


class ResumeVersion(Base):
    __tablename__ = "resume_versions"
    __table_args__ = (UniqueConstraint("resume_id", "version_number", name="uq_resume_version_number"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ver"))
    resume_id: Mapped[str] = mapped_column(ForeignKey("resumes.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[dict] = mapped_column(JSON)
    facts: Mapped[list] = mapped_column(JSON, default=list)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    changed_fields: Mapped[list] = mapped_column(JSON, default=list)
    agent: Mapped[str] = mapped_column(String(80), default="system")
    approval_status: Mapped[str] = mapped_column(String(40), default="approved")
    kind: Mapped[str] = mapped_column(String(40), default="accepted")
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    undo_of_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    undone_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    resume: Mapped[Resume] = relationship(back_populates="versions")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("conv"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    resume_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    target_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    target_role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    job_description_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobDescription(Base):
    __tablename__ = "job_descriptions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("jd"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    resume_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(250), default="Job description")
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("run"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    resume_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    base_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent: Mapped[str] = mapped_column(String(60), default="GENERAL_CHAT")
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    user_message: Mapped[str] = mapped_column(Text, default="")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    agent_outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    final_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_seq", "run_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("run_id", name="uq_proposal_run"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("prop"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    resume_id: Mapped[str] = mapped_column(ForeignKey("resumes.id"), index=True)
    base_version_id: Mapped[str] = mapped_column(String(64))
    changes: Mapped[list] = mapped_column(JSON, default=list)
    candidate_content: Mapped[dict] = mapped_column(JSON)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateBucket(Base):
    """Durable, best-effort rate limiting bucket (single-node friendly)."""

    __tablename__ = "rate_buckets"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[float] = mapped_column(Float, default=0.0)
    count: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
