from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_current_user, owned_resume
from ..config import settings
from ..db import get_db
from ..export import build_docx, build_pdf, export_filename
from ..models import Resume, ResumeVersion, User, new_id, utcnow
from ..parsing import DocumentError, parse_resume
from ..resume_service import (
    create_version,
    diff_contents,
    latest_version,
    redo,
    supersede_pending_proposals,
    undo,
)
from ..schemas import CompareRequest, RestoreRequest
from ..security import detect_prompt_injection
from ..serializers import iso, resume_out, resume_summary, version_state, version_to_dict

logger = logging.getLogger("resume_studio.resumes")
router = APIRouter(prefix="/resumes", tags=["resumes"])


def _version_belongs(db: Session, resume: Resume, version_id: str) -> ResumeVersion:
    version = db.get(ResumeVersion, version_id)
    if not version or version.resume_id != resume.id:
        raise HTTPException(status_code=404, detail="Version not found.")
    return version


@router.post("/upload", status_code=201)
async def upload_resume(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail={"code": "FILE_TOO_LARGE", "message": f"File exceeds the {settings.max_upload_mb} MB limit."},
        )
    try:
        parsed = parse_resume(data, file.filename or "resume", file.content_type)
    except DocumentError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Resume parsing failed")
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PARSING_FAILED",
                "message": "The document could not be parsed safely. Try exporting it again as PDF/DOCX.",
            },
        ) from exc

    checksum = parsed.meta.get("checksum")
    duplicate = db.scalar(
        select(Resume).where(
            Resume.user_id == user.id,
            Resume.checksum == checksum,
            Resume.deleted_at.is_(None),
        )
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DUPLICATE_RESUME",
                "message": "This exact file is already in your workspace.",
                "existing_resume_id": duplicate.id,
            },
        )

    resume_id = new_id("res")
    safe_name = parsed.meta.get("filename") or "resume.pdf"
    directory = settings.uploads_dir / user.id / resume_id
    directory.mkdir(parents=True, exist_ok=True)
    storage_path = directory / safe_name
    storage_path.write_bytes(data)

    notices = detect_prompt_injection(parsed.raw_text)
    warnings = list(parsed.meta.get("warnings") or [])
    if notices:
        warnings.extend(notices)
        warnings.append("Instruction-like text found in the document was treated as untrusted data and was not executed.")

    meta = {
        "page_count": parsed.meta.get("page_count"),
        "ocr_used": parsed.meta.get("ocr_used", False),
        "confidence": parsed.meta.get("confidence"),
        "detected_sections": parsed.meta.get("detected_sections", []),
        "warnings": warnings,
        "raw_text_length": len(parsed.raw_text),
        "security_notices": notices,
    }
    resume = Resume(
        id=resume_id,
        user_id=user.id,
        title=(parsed.content.get("personal_information", {}).get("full_name") or Path(safe_name).stem)[:200],
        original_filename=safe_name,
        mime_type=parsed.meta.get("mime_type") or "application/octet-stream",
        storage_path=str(storage_path),
        size_bytes=len(data),
        checksum=checksum,
        extraction_meta=meta,
    )
    db.add(resume)
    db.flush()
    try:
        version = create_version(
            db,
            resume,
            parsed.content,
            summary="Original upload (immutable baseline)",
            changed_fields=[],
            agent="system",
            kind="original",
            parent_version_id=None,
        )
    except Exception:
        db.rollback()
        try:
            storage_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    logger.info("Resume %s uploaded by user %s (%s bytes)", resume.id, user.id, len(data))
    return {
        "resume": resume_out(resume, version, include_content=True),
        "preview": parsed.content,
        "warnings": warnings,
    }


@router.get("")
def list_resumes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    resumes = db.scalars(
        select(Resume)
        .where(Resume.user_id == user.id, Resume.deleted_at.is_(None))
        .order_by(Resume.created_at.desc())
    ).all()
    counts = dict(
        db.execute(
            select(ResumeVersion.resume_id, func.count(ResumeVersion.id))
            .where(ResumeVersion.resume_id.in_([r.id for r in resumes] or ["none"]))
            .group_by(ResumeVersion.resume_id)
        ).all()
    )
    return [resume_summary(resume, counts.get(resume.id, 0)) for resume in resumes]


@router.get("/{resume_id}")
def get_resume(
    resume_id: str,
    include_content: bool = Query(default=True),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = owned_resume(db, resume_id, user.id)
    version = latest_version(db, resume)
    return resume_out(resume, version, include_content=include_content)


@router.patch("/{resume_id}")
def rename_resume(
    resume_id: str,
    payload: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = owned_resume(db, resume_id, user.id)
    title = str(payload.get("title", "")).strip()
    if not 1 <= len(title) <= 200:
        raise HTTPException(status_code=400, detail="Title must be between 1 and 200 characters.")
    resume.title = title
    db.commit()
    return resume_out(resume, latest_version(db, resume))


@router.get("/{resume_id}/versions")
def list_versions(
    resume_id: str,
    include_content: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = owned_resume(db, resume_id, user.id)
    versions = db.scalars(
        select(ResumeVersion)
        .where(ResumeVersion.resume_id == resume.id)
        .order_by(ResumeVersion.version_number.desc())
    ).all()
    return {
        "versions": [
            version_to_dict(v, include_content=include_content, active=v.id == resume.active_version_id)
            for v in versions
        ],
        "active_version_id": resume.active_version_id,
        **version_state(db, resume),
    }


@router.get("/{resume_id}/versions/{version_id}")
def get_version(
    resume_id: str,
    version_id: str,
    include_content: bool = Query(default=True),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = owned_resume(db, resume_id, user.id)
    version = _version_belongs(db, resume, version_id)
    return version_to_dict(version, include_content=include_content, active=version.id == resume.active_version_id)


@router.post("/{resume_id}/compare")
def compare_versions(
    resume_id: str,
    payload: CompareRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = owned_resume(db, resume_id, user.id)
    version_a = _version_belongs(db, resume, payload.version_a)
    version_b = _version_belongs(db, resume, payload.version_b)
    return {
        "version_a": version_to_dict(version_a, active=False),
        "version_b": version_to_dict(version_b, active=False),
        "diff": diff_contents(version_a.content, version_b.content),
    }


@router.post("/{resume_id}/restore")
def restore_version(
    resume_id: str,
    payload: RestoreRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = owned_resume(db, resume_id, user.id)
    version = _version_belongs(db, resume, payload.version_id)
    if version.id == resume.active_version_id:
        raise HTTPException(status_code=400, detail="That version is already active.")
    supersede_pending_proposals(db, resume.id, commit=False)
    restored = create_version(
        db,
        resume,
        version.content,
        summary=f"Restored version {version.version_number}",
        changed_fields=version.changed_fields,
        agent="system",
        kind="restore",
        parent_version_id=resume.active_version_id,
        undo_of_version_id=version.id,
    )
    return {
        "version": version_to_dict(restored, include_content=True, active=True),
        **version_state(db, resume),
    }


@router.post("/{resume_id}/undo")
def undo_resume(
    resume_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = owned_resume(db, resume_id, user.id)
    try:
        supersede_pending_proposals(db, resume.id, commit=False)
        version = undo(db, resume, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "version": version_to_dict(version, include_content=True, active=True),
        "message": f"Restored version {version.version_number} as a new immutable revision.",
        **version_state(db, resume),
    }


@router.post("/{resume_id}/redo")
def redo_resume(
    resume_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = owned_resume(db, resume_id, user.id)
    try:
        supersede_pending_proposals(db, resume.id, commit=False)
        version = redo(db, resume, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "version": version_to_dict(version, include_content=True, active=True),
        "message": f"Re-applied version content as v{version.version_number}.",
        **version_state(db, resume),
    }


@router.get("/{resume_id}/export")
def export_resume(
    resume_id: str,
    format: str = Query(default="docx", pattern="^(docx|pdf)$"),
    version_id: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = owned_resume(db, resume_id, user.id)
    version = _version_belongs(db, resume, version_id) if version_id else latest_version(db, resume)
    if not version:
        raise HTTPException(status_code=409, detail="This resume has no saved version to export.")
    try:
        payload = build_docx(version.content, resume.title) if format == "docx" else build_pdf(version.content, resume.title)
    except Exception as exc:  # pragma: no cover - font/platform specific
        logger.warning("Export failed for resume %s: %s", resume.id, type(exc).__name__)
        raise HTTPException(
            status_code=422,
            detail="Export failed. DOCX is the most reliable format for this content.",
        ) from exc

    export_dir = settings.exports_dir / user.id / resume.id
    export_dir.mkdir(parents=True, exist_ok=True)
    filename = export_filename(f"{resume.title}_v{version.version_number}", format)
    (export_dir / f"{version.id}_{filename}").write_bytes(payload)

    disposition = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quote(filename)}'
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if format == "docx" else "application/pdf"
    )
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{resume_id}")
def delete_resume(
    resume_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = owned_resume(db, resume_id, user.id)
    supersede_pending_proposals(db, resume.id, commit=False)
    resume.deleted_at = utcnow()
    db.commit()
    try:
        Path(resume.storage_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove original file for resume %s", resume.id)
    return {"deleted": True, "resume_id": resume.id, "deleted_at": iso(resume.deleted_at)}
